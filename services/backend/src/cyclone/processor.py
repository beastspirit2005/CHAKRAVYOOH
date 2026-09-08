"""
Chakravyooh Cyclone Intelligence Processor
Decoupled from router to prevent circular imports with replay engine.
"""
import asyncio

from sqlalchemy import select

from src.cyclone.contracts import CycloneIntelligence
from src.cyclone.store import save_intelligence_frame, save_zone_risks
from src.cyclone.risk_engine import evaluate_risk
from src.cyclone.alerts import generate_alerts
from src.cyclone.events import get_intelligence_events, get_risk_updated_event
from src.zones.severity.state import elevate_zone_for_cyclone
from src.database.models import Zone
from src.database.session import async_session_maker
from src.realtime.connection_manager import manager as ws_manager


async def process_cyclone_intelligence(intelligence: CycloneIntelligence, intelligence_json: dict):
    """
    Core cyclone intelligence processing pipeline.
    Uses an isolated DB session, applies atomic commit, and broadcasts events
    via the Outbox Pattern (events emitted only after successful commit).
    """
    async with async_session_maker() as db:
        try:
            is_new, latest = await save_intelligence_frame(db, intelligence_json)
            if not is_new:
                return  # Idempotent: already processed this frame

            events_to_broadcast = []

            # Collect events for raw AI intelligence
            events_to_broadcast.extend(get_intelligence_events(intelligence))

            # Evaluate Risk for all Zones
            risks = await evaluate_risk(db, intelligence)
            if risks:
                await save_zone_risks(db, risks)

                # Enforce No-Downgrade Zone Elevation
                zone_ids = [r.zone_id for r in risks]
                stmt = select(Zone).where(Zone.zone_id.in_(zone_ids))
                zones = (await db.execute(stmt)).scalars().all()
                zone_map = {z.zone_id: z for z in zones}

                for risk in risks:
                    zone = zone_map.get(risk.zone_id)
                    if zone:
                        elevated, evts = await elevate_zone_for_cyclone(zone, risk.risk_level)
                        if elevated:
                            events_to_broadcast.extend(evts)

                # Generate Signed Alerts and Versioned Warnings
                _, alert_evts = await generate_alerts(db, intelligence, risks)
                events_to_broadcast.extend(alert_evts)

                # Collect Risk Updated event
                events_to_broadcast.append(get_risk_updated_event(intelligence.cyclone_id, risks))

            # Commit everything atomically
            await db.commit()

            # Broadcast real-time events (Outbox Pattern - after commit)
            for evt in events_to_broadcast:
                await ws_manager.broadcast(evt)

        except Exception:
            await db.rollback()
            raise

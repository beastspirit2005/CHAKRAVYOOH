from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.session import get_db
from src.auth.rbac import get_current_user
from src.database.models import User
from src.cyclone.contracts import CycloneIntelligence
from src.cyclone.store import (
    save_intelligence_frame,
    save_zone_risks,
    get_active_cyclones,
    get_zone_risks,
    get_active_alerts
)
from src.cyclone.risk_engine import evaluate_risk
from src.cyclone.alerts import generate_alerts, get_authority_public_key_hex, AUTHORITY_KEY_ID
from src.cyclone.processor import process_cyclone_intelligence
from src.database.session import async_session_maker
from src.realtime.connection_manager import manager as ws_manager
from src.cyclone.events import get_intelligence_events, get_risk_updated_event
from src.zones.severity.state import elevate_zone_for_cyclone
from src.database.models import Zone
from sqlalchemy import select
from src.cyclone.replay import init_replay_routes

router = APIRouter(prefix="/cyclone", tags=["Cyclone Intelligence"])
init_replay_routes(router)


@router.get("/risk-matrix", tags=["Cyclone Intelligence"])
async def get_cyclone_risk_matrix():
    """
    Authoritative 2D Geospatial Cyclone Impact & Evacuation Risk Matrix.
    Called by the God's Eye frontend UI (index.html) to render the tactical risk overlay.
    """
    return {
        "meta": {
            "version": "1.0",
            "cyclone": "DANA-2 (Odisha Coast Approach)",
            "generated_at": "2026-09-09T00:00:00Z",
            "axes": {
                "x": {"label": "Population Density Zone", "values": [
                    {"id": "c1", "label": "Inland Rural", "density": "<200/km²"},
                    {"id": "c2", "label": "Semi-Urban Corridor", "density": "200–800/km²"},
                    {"id": "c3", "label": "Dense Urban Center", "density": ">2,000/km²"},
                    {"id": "c4", "label": "Critical Coastal Front", "density": "Coastline <2km"},
                ]}
            }
        },
        "matrix_grid": [
            {"tier": "Catastrophic (>3.5m)", "surge_level": ">3.5m", "cells": [
                {"col": "Inland Rural", "risk_level": "MODERATE", "badge": "YELLOW", "code": "C1", "zone": "Zone C", "evac_target": "50% Evacuation"},
                {"col": "Semi-Urban Corridor", "risk_level": "HIGH", "badge": "ORANGE", "code": "B1", "zone": "Zone B", "evac_target": "80% Evacuation"},
                {"col": "Dense Urban Center", "risk_level": "EXTREME", "badge": "RED", "code": "A2", "zone": "Zone A", "evac_target": "100% Mandatory"},
                {"col": "Critical Coastal Front", "risk_level": "EXTREME", "badge": "RED", "code": "A1", "zone": "Zone A", "evac_target": "100% Evacuated"},
            ]},
            {"tier": "Severe (2.5–3.5m)", "surge_level": "2.5–3.5m", "cells": [
                {"col": "Inland Rural", "risk_level": "LOW", "badge": "GREEN", "code": "D1", "zone": "Zone D", "evac_target": "Advisory Only"},
                {"col": "Semi-Urban Corridor", "risk_level": "MODERATE", "badge": "YELLOW", "code": "C2", "zone": "Zone C", "evac_target": "Precautionary"},
                {"col": "Dense Urban Center", "risk_level": "HIGH", "badge": "ORANGE", "code": "B2", "zone": "Zone B", "evac_target": "75% Evacuation"},
                {"col": "Critical Coastal Front", "risk_level": "EXTREME", "badge": "RED", "code": "A3", "zone": "Zone A", "evac_target": "100% Mandatory"},
            ]},
            {"tier": "Moderate (1.5–2.5m)", "surge_level": "1.5–2.5m", "cells": [
                {"col": "Inland Rural", "risk_level": "LOW", "badge": "GREEN", "code": "D2", "zone": "Zone D", "evac_target": "Normal Standby"},
                {"col": "Semi-Urban Corridor", "risk_level": "LOW", "badge": "GREEN", "code": "D3", "zone": "Zone D", "evac_target": "Advisory Only"},
                {"col": "Dense Urban Center", "risk_level": "MODERATE", "badge": "YELLOW", "code": "C3", "zone": "Zone C", "evac_target": "Precautionary"},
                {"col": "Critical Coastal Front", "risk_level": "HIGH", "badge": "ORANGE", "code": "B3", "zone": "Zone B", "evac_target": "Mandatory Coastal"},
            ]},
            {"tier": "Minor (<1.5m)", "surge_level": "<1.5m", "cells": [
                {"col": "Inland Rural", "risk_level": "LOW", "badge": "GREEN", "code": "D4", "zone": "Zone D", "evac_target": "Normal Ops"},
                {"col": "Semi-Urban Corridor", "risk_level": "LOW", "badge": "GREEN", "code": "D5", "zone": "Zone D", "evac_target": "Normal Ops"},
                {"col": "Dense Urban Center", "risk_level": "LOW", "badge": "GREEN", "code": "D6", "zone": "Zone D", "evac_target": "Advisory"},
                {"col": "Critical Coastal Front", "risk_level": "MODERATE", "badge": "YELLOW", "code": "C4", "zone": "Zone C", "evac_target": "Beach Closure"},
            ]},
        ],
        "risk_zones": [
            {"id": "zone-a", "code": "ZONE A", "level": "EXTREME", "colorTag": "RED WARNING",
             "corridor": "Coastal Strip 0–15 km (Gopalpur, Ganjam, Chatrapur)",
             "surgeHeight": "3.8m above astronomical tide", "sustainedWinds": "135–155 km/h",
             "populationExposed": "248,500", "evacuatedPercent": 82, "sheltersActive": 46},
            {"id": "zone-b", "code": "ZONE B", "level": "HIGH", "colorTag": "ORANGE ALERT",
             "corridor": "Inland Belt 15–40 km (Berhampur City, Aska Corridor)",
             "surgeHeight": "Flash inundation up to 1.4m", "sustainedWinds": "100–125 km/h",
             "populationExposed": "620,000", "evacuatedPercent": 56, "sheltersActive": 84},
            {"id": "zone-c", "code": "ZONE C", "level": "MODERATE", "colorTag": "YELLOW WATCH",
             "corridor": "Perimeter 40–90 km (Digapahandi, Bhanjanagar)",
             "surgeHeight": "Inland heavy rainfall >180mm/24h", "sustainedWinds": "70–95 km/h",
             "populationExposed": "1,140,000", "evacuatedPercent": 24, "sheltersActive": 110},
            {"id": "zone-d", "code": "ZONE D", "level": "LOW", "colorTag": "GREEN STANDBY",
             "corridor": "Inland Highlands >90 km (Mohana, Rayagada Gateway)",
             "surgeHeight": "No marine surge; moderate rainfall", "sustainedWinds": "45–65 km/h",
             "populationExposed": "350,000", "evacuatedPercent": 10, "sheltersActive": 58},
        ]
    }


from typing import Optional
from fastapi import Header
from src.config import get_settings

async def get_ingestion_user(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Authenticates ingestion callers via either:
    1. Machine-to-machine System API Key (CHAKRAVYUH_SYSTEM_API_KEY)
    2. Interactive Admin / System User JWT
    """
    settings = get_settings()
    token = None
    if authorization:
        if authorization.startswith("Bearer ") or authorization.startswith("ApiKey "):
            token = authorization.split(" ", 1)[1].strip()
        else:
            token = authorization.strip()
    elif x_api_key:
        token = x_api_key.strip()
        
    if token and settings.CHAKRAVYUH_SYSTEM_API_KEY and token == settings.CHAKRAVYUH_SYSTEM_API_KEY:
        return User(user_id="system-ml-producer", role="system", is_active=True)
        
    if token:
        try:
            return await get_current_user(token=token, db=db)
        except HTTPException:
            pass
            
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid System API Key or Admin/System JWT required for intelligence ingestion",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/intelligence", status_code=status.HTTP_202_ACCEPTED)
@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_intelligence(
    intelligence: CycloneIntelligence,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_ingestion_user)
):
    """
    Project Chakravyooh: Ingests intelligence from the AI engine.
    Idempotent. Processed asynchronously.
    """
    if user.role not in ["admin", "system"]:
        raise HTTPException(status_code=403, detail="Only system accounts can ingest AI intelligence")
        
    # Offload processing to background task to immediately ACK the AI engine
    background_tasks.add_task(process_cyclone_intelligence, intelligence, intelligence.model_dump(mode='json'))
    return {"status": "accepted", "cyclone_id": intelligence.cyclone_id}

@router.get("/active")
async def list_active_cyclones(db: AsyncSession = Depends(get_db)):
    """
    Returns the latest state of all tracked cyclones, including zone risks and alerts.
    """
    cyclones = await get_active_cyclones(db)
    result = []
    
    for c in cyclones:
        risks = await get_zone_risks(db, c.cyclone_id)
        alerts = await get_active_alerts(db, c.cyclone_id)
        
        result.append({
            "cyclone_id": c.cyclone_id,
            "name": c.name,
            "stage": c.stage,
            "intensity_level": c.intensity_level,
            "current_lat": c.current_lat,
            "current_lon": c.current_lon,
            "timestamp": c.timestamp,
            "risks": [
                {
                    "zone_id": r.zone_id,
                    "zone_name": r.zone_name,
                    "risk_level": r.risk_level,
                    "distance_km": r.distance_km,
                    "eta_hours": r.eta_hours
                } for r in risks
            ],
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "type": a.type,
                    "message": a.message,
                    "priority": a.priority,
                    "zones": a.zone_ids
                } for a in alerts
            ]
        })
        
    return result

@router.get("/alerts")
async def list_alerts(db: AsyncSession = Depends(get_db)):
    """Returns all ACTIVE cyclone warning alerts with signature data for client verification."""
    alerts = await get_active_alerts(db)
    return alerts

@router.get("/alerts/authority-key")
async def get_alert_authority_key():
    """
    Returns the Ed25519 public key used by the Chakravyooh authority to sign all CYCLONE_WARNING
    alerts (Master PRD §22). Android and mesh clients should cache this key and use it to verify
    alert signatures offline before accepting or relaying any warning.
    """
    return {
        "authority_key_id": AUTHORITY_KEY_ID,
        "public_key_hex": get_authority_public_key_hex(),
        "algorithm": "Ed25519",
        "usage": "Verify the `signature` field in CYCLONE_WARNING alerts against this public key.",
    }

@router.get("/{id}")
async def get_cyclone(id: str, db: AsyncSession = Depends(get_db)):
    from src.database.models import CycloneLatest
    stmt = select(CycloneLatest).where(CycloneLatest.cyclone_id == id)
    c = (await db.execute(stmt)).scalars().first()
    if not c:
        raise HTTPException(404, "Cyclone not found")
    return c

@router.get("/{id}/risk")
async def get_cyclone_risk(id: str, db: AsyncSession = Depends(get_db)):
    risks = await get_zone_risks(db, id)
    return risks

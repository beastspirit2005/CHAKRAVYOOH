"""
Chakravyooh Alert Engine ??? Master PRD ??20, ??21, ??22
Generates, versions, and cryptographically signs CYCLONE_WARNING alerts.
"""
import json
import os
import time
from datetime import datetime, timezone, timedelta

try:
    import nacl.encoding
    import nacl.signing
    HAS_NACL = True
except ImportError:
    HAS_NACL = False
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Alert, CycloneZoneRisk
from src.cyclone.contracts import CycloneIntelligence
from src.realtime.events import RealtimeEvent, RealtimeEventType

# ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
# Authority Signing Key ??? Master PRD ??22
# In production: loaded from encrypted environment variable (CHAKRAVYOOH_AUTHORITY_SIGNING_KEY_HEX)
# In development/hackathon: deterministically derived so the public key is stable across restarts.
# ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

_AUTHORITY_KEY_HEX = os.environ.get("CHAKRAVYOOH_AUTHORITY_SIGNING_KEY_HEX", "")

def _get_authority_signing_key():
    if not HAS_NACL: return None
    """Returns the backend authority Ed25519 signing key."""
    if _AUTHORITY_KEY_HEX and len(_AUTHORITY_KEY_HEX) == 64:
        return nacl.signing.SigningKey(bytes.fromhex(_AUTHORITY_KEY_HEX))
    # Fallback: deterministic dev key (NOT for production use)
    return nacl.signing.SigningKey(b"chakravyooh-authority-key-dev-00")

def get_authority_public_key_hex() -> str:
    """Returns the hex-encoded Ed25519 public key for client-side alert verification."""
    signing_key = _get_authority_signing_key()
    return signing_key.verify_key.encode(nacl.encoding.HexEncoder).decode()

AUTHORITY_KEY_ID = "chakravyooh-authority-v1"

def _sign_alert_canonical(alert_id: str, version: int, cyclone_id: str,
                           priority: str, message: str, zone_ids: list,
                           valid_until: datetime) -> str:
    """
    Canonical serialization of an alert for signing (Master PRD ??22).
    Uses a deterministic JSON string to prevent signature malleability.
    """
    canonical = json.dumps({
        "alert_id": alert_id,
        "version": version,
        "cyclone_id": cyclone_id,
        "priority": priority,
        "message": message,
        "zone_ids": sorted(zone_ids),
        "valid_until": valid_until.isoformat() if valid_until else None,
        "authority_key_id": AUTHORITY_KEY_ID,
    }, sort_keys=True, separators=(",", ":"))
    signing_key = _get_authority_signing_key()
    signed = signing_key.sign(canonical.encode())
    # Return only the 64-byte signature prefix (not the message)
    return signed.signature.hex()


async def generate_alerts(
    db: AsyncSession,
    intelligence: CycloneIntelligence,
    risks: list[CycloneZoneRisk]
):
    """
    Generates cryptographically signed CYCLONE_WARNING alerts for HIGH/EXTREME zones.
    Implements alert versioning: if priority escalates, supersedes existing active alert.
    Returns (new_alerts, events_to_broadcast).
    """
    new_alerts = []
    events_to_broadcast = []

    # Only alert on HIGH, CRITICAL, or EXTREME zones
    alert_zones = [r for r in risks if r.risk_level in ["EXTREME", "CRITICAL", "HIGH"]]
    if not alert_zones:
        return new_alerts, events_to_broadcast

    cyc_id = intelligence.cyclone_id
    stage = intelligence.classification.stage if intelligence.classification else "Tropical Cyclone"
    zone_ids = [r.zone_id for r in alert_zones]

    # Determine highest risk level
    highest_risk = "HIGH"
    if any(r.risk_level == "EXTREME" for r in alert_zones):
        highest_risk = "EXTREME"
    elif any(r.risk_level == "CRITICAL" for r in alert_zones):
        highest_risk = "CRITICAL"

    # Compute valid_until: 24h from now (Master PRD ??21)
    valid_until = datetime.now(timezone.utc) + timedelta(seconds=86400)

    # Check for an existing ACTIVE alert for this cyclone
    stmt = select(Alert).where(
        Alert.cyclone_id == cyc_id,
        Alert.status == "ACTIVE",
    ).order_by(Alert.issued_at.desc())
    existing = (await db.execute(stmt)).scalars().first()

    if existing:
        if existing.priority == highest_risk:
            # Same priority ??? deduplication: no new alert needed
            return new_alerts, events_to_broadcast

        # Priority has changed ??? supersede the existing alert (Master PRD ??21)
        await db.execute(
            update(Alert)
            .where(Alert.alert_id == existing.alert_id)
            .values(status="SUPERSEDED", updated_at=datetime.now(timezone.utc))
        )
        superseded_event = RealtimeEvent(
            event_type=RealtimeEventType.CYCLONE_ALERT_SUPERSEDED.value,
            payload={"alert_id": existing.alert_id, "cyclone_id": cyc_id,
                     "superseded_by": "pending"},
            timestamp=int(time.time() * 1000)
        )
        events_to_broadcast.append(superseded_event)
        new_version = existing.version + 1
        supersedes_id = existing.alert_id
    else:
        new_version = 1
        supersedes_id = None

    # Build warning message
    zone_names = ", ".join([r.zone_name or "Unknown" for r in alert_zones[:3]])
    if len(alert_zones) > 3:
        zone_names += " and others"
    message = (
        f"CYCLONE WARNING ??? {stage} approaching {zone_names}. "
        f"Risk: {highest_risk}. Seek shelter immediately. "
        f"This warning remains valid if cellular networks fail ??? "
        f"relay via Chakravyooh mesh."
    )

    # Create the alert record (flush to get alert_id before signing)
    alert = Alert(
        type="CYCLONE_WARNING",
        cyclone_id=cyc_id,
        stage=stage,
        message=message,
        zone_ids=zone_ids,
        priority=highest_risk,
        status="ACTIVE",
        version=new_version,
        supersedes=supersedes_id,
        valid_until=valid_until,
        ttl_s=86400,
        authority_key_id=AUTHORITY_KEY_ID,
    )
    db.add(alert)
    await db.flush()  # Populate alert_id

    # Cryptographically sign the alert (Master PRD ??22)
    signature_hex = _sign_alert_canonical(
        alert_id=alert.alert_id,
        version=new_version,
        cyclone_id=cyc_id,
        priority=highest_risk,
        message=message,
        zone_ids=zone_ids,
        valid_until=valid_until,
    )
    alert.signature = signature_hex
    new_alerts.append(alert)

    # Update the superseded event with the new alert_id
    if supersedes_id:
        for evt in events_to_broadcast:
            if evt.event_type == RealtimeEventType.CYCLONE_ALERT_SUPERSEDED.value:
                evt.payload["superseded_by"] = alert.alert_id

    # Emit CYCLONE_ALERT_CREATED or CYCLONE_ALERT_UPDATED
    event_type = (RealtimeEventType.CYCLONE_ALERT_UPDATED.value
                  if new_version > 1 else RealtimeEventType.CYCLONE_ALERT_CREATED.value)
    events_to_broadcast.append(RealtimeEvent(
        event_type=event_type,
        payload={
            "alert_id": alert.alert_id,
            "version": new_version,
            "cyclone_id": cyc_id,
            "message": message,
            "zones": zone_ids,
            "priority": highest_risk,
            "valid_until": valid_until.isoformat(),
            "signature": signature_hex,
            "authority_key_id": AUTHORITY_KEY_ID,
            "supersedes": supersedes_id,
        },
        timestamp=int(time.time() * 1000)
    ))

    return new_alerts, events_to_broadcast






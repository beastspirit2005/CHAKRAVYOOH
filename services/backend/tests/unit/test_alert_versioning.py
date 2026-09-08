"""
Tests -- Alert Versioning and Lifecycle (Master PRD S20, S21)
Verifies version increment and ACTIVE -> SUPERSEDED state transitions.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from src.cyclone.alerts import generate_alerts
from src.cyclone.contracts import CycloneIntelligence, CycloneIdentification, CycloneClassification


def _make_intelligence(cyclone_id="CYC-TEST-001"):
    return CycloneIntelligence(
        cyclone_id=cyclone_id,
        name="Test-Cyclone",
        timestamp=datetime.now(timezone.utc),
        basin="North Indian Ocean",
        identification=CycloneIdentification(detected=True, confidence=0.95),
        classification=CycloneClassification(stage="MATURE_TROPICAL_CYCLONE", confidence=0.91),
        tier="tier1",
    )


def _make_risk(zone_id="Z-A", risk_level="EXTREME", zone_name="Puri"):
    risk = MagicMock()
    risk.zone_id = zone_id
    risk.zone_name = zone_name
    risk.risk_level = risk_level
    return risk


@pytest.mark.asyncio
async def test_first_alert_has_version_1():
    """First alert for a cyclone must start at version=1 with no supersedes."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None  # No existing alert
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()

    intelligence = _make_intelligence()
    risks = [_make_risk()]

    new_alerts, events = await generate_alerts(db, intelligence, risks)

    assert len(new_alerts) == 1
    assert new_alerts[0].version == 1
    assert new_alerts[0].supersedes is None
    assert new_alerts[0].status == "ACTIVE"
    assert new_alerts[0].signature is not None


@pytest.mark.asyncio
async def test_same_priority_no_new_alert():
    """If an ACTIVE alert with same priority already exists, no new alert is created."""
    db = AsyncMock()
    existing = MagicMock()
    existing.priority = "EXTREME"
    existing.version = 1
    existing.alert_id = "ALT-existing-001"
    result = MagicMock()
    result.scalars.return_value.first.return_value = existing
    db.execute = AsyncMock(return_value=result)

    intelligence = _make_intelligence()
    risks = [_make_risk(risk_level="EXTREME")]

    new_alerts, events = await generate_alerts(db, intelligence, risks)

    assert len(new_alerts) == 0
    assert len(events) == 0


@pytest.mark.asyncio
async def test_escalation_creates_version_2_and_supersedes():
    """Priority escalation from HIGH to EXTREME should create v2 and supersede v1."""
    db = AsyncMock()
    existing = MagicMock()
    existing.priority = "HIGH"  # Lower priority than incoming EXTREME
    existing.version = 1
    existing.alert_id = "ALT-existing-002"
    result_select = MagicMock()
    result_select.scalars.return_value.first.return_value = existing
    result_update = MagicMock()
    db.execute = AsyncMock(side_effect=[result_select, result_update])
    db.flush = AsyncMock()

    intelligence = _make_intelligence()
    risks = [_make_risk(risk_level="EXTREME")]

    new_alerts, events = await generate_alerts(db, intelligence, risks)

    assert len(new_alerts) == 1
    assert new_alerts[0].version == 2
    assert new_alerts[0].supersedes == "ALT-existing-002"
    # Events: SUPERSEDED + UPDATED
    event_types = [e.event_type for e in events]
    assert "CYCLONE_ALERT_SUPERSEDED" in event_types
    assert "CYCLONE_ALERT_UPDATED" in event_types

"""
Tests -- Demo Replay Engine (Master PRD S42)
Verifies the 4-frame Amphan replay builds correct intelligence payloads.
"""
import pytest
from datetime import datetime, timezone

from src.cyclone.replay import _build_frames


def test_replay_builds_four_frames():
    frames = _build_frames()
    assert len(frames) == 4


def test_frames_share_same_cyclone_id():
    frames = _build_frames()
    ids = {f.cyclone_id for f in frames}
    assert len(ids) == 1  # All frames belong to same cyclone


def test_frames_have_sequential_demo_labels():
    frames = _build_frames()
    for i, frame in enumerate(frames, start=1):
        assert frame.extra["demo_frame"] == i


def test_frame_timestamps_are_ascending():
    frames = _build_frames()
    timestamps = [f.timestamp for f in frames]
    for a, b in zip(timestamps, timestamps[1:]):
        assert a < b


def test_frame1_is_detection_stage():
    frame = _build_frames()[0]
    assert frame.identification.detected is True
    assert frame.classification.stage == "DEVELOPING_DISTURBANCE"
    assert frame.identification.confidence == pytest.approx(0.94)


def test_frame3_triggers_risk_zones():
    """Frame 3 (T-24h) must have strong enough trajectory data to drive risk evaluation."""
    frame = _build_frames()[2]
    assert frame.prediction is not None
    assert len(frame.prediction.predicted_path) >= 3
    # Cone must start at 0.0 (required by risk engine)
    assert frame.prediction.uncertainty.cone_radius_km[0] == pytest.approx(0.0)


def test_frame4_is_most_intense():
    """Frame 4 (T-12h) must have highest intensity and confidence."""
    frames = _build_frames()
    assert frames[3].intensity.max_wind_kt > frames[0].intensity.max_wind_kt
    assert frames[3].identification.confidence > frames[0].identification.confidence


def test_all_frames_have_tier1():
    for frame in _build_frames():
        assert frame.tier == "tier1"


def test_all_frames_have_freshness():
    for frame in _build_frames():
        assert frame.freshness is not None
        assert frame.freshness.generated_at is not None

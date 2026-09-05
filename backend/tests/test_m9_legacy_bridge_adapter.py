"""M9.0 bridge adapter tests.

Covers the §9 required cases with tiny synthetic dictionaries:
valid conversion to seconds + Hz, canonical class mapping, invalid class_id
rejected, invalid time bbox rejected, invalid frequency bbox rejected, bbox
outside the selected Recording rejected, missing confidence rejected (not
fabricated), and sample identity mismatch rejected.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.m9_legacy_bridge.adapter import (  # noqa: E402
    LegacyClassError,
    LegacyConfidenceMissing,
    LegacyCoordinateError,
    LegacyDetectionAdapter,
    LegacyRecordError,
    LegacySampleIdentityMismatch,
    load_label_space,
)
from research.m9_legacy_bridge.schema import PlatformDetection, RecordingContext  # noqa: E402

LABEL_SPACE = REPO_ROOT / "label_spaces" / "spacenet_14.json"


@pytest.fixture(scope="module")
def label_space() -> dict[int, str]:
    return load_label_space(LABEL_SPACE)


@pytest.fixture()
def recording() -> RecordingContext:
    return RecordingContext(
        name="0",
        duration_s=0.15,
        frequency_low_hz=2430.0e6,
        frequency_high_hz=2480.0e6,
    )


@pytest.fixture()
def adapter(recording, label_space) -> LegacyDetectionAdapter:
    return LegacyDetectionAdapter(recording=recording, label_space=label_space)


def _valid_record() -> dict:
    return {
        "sample_id": "0",
        "t0_s": 0.005,
        "t1_s": 0.012,
        "f0_hz": 2456.97385e6,
        "f1_hz": 2457.02615e6,
        "class_id": 9,
        "score": 0.88,
    }


def test_valid_detection_becomes_seconds_and_absolute_hz(adapter):
    result = adapter.adapt(_valid_record())
    assert isinstance(result, PlatformDetection)
    assert result.t_start_s == 0.005
    assert result.t_end_s == 0.012
    assert result.f_low_hz == 2456973850.0
    assert result.f_high_hz == 2457026150.0
    assert result.class_id == 9
    assert result.class_name == "LoRa 250kHz"
    assert result.confidence == 0.88


def test_canonical_class_mapping(adapter):
    expected = load_label_space(LABEL_SPACE)
    for class_id, class_name in expected.items():
        record = _valid_record()
        record["class_id"] = class_id
        record["t0_s"] = 0.001
        record["t1_s"] = 0.002
        record["f0_hz"] = 2440.0e6
        record["f1_hz"] = 2440.1e6
        result = adapter.adapt(record)
        assert result.class_id == class_id
        assert result.class_name == class_name


def test_invalid_class_id_rejected(adapter):
    record = _valid_record()
    record["class_id"] = 14
    with pytest.raises(LegacyClassError):
        adapter.adapt(record)


def test_negative_time_bbox_rejected(adapter):
    record = _valid_record()
    record["t0_s"] = -0.001
    with pytest.raises(LegacyCoordinateError):
        adapter.adapt(record)


def test_zero_width_time_bbox_rejected(adapter):
    record = _valid_record()
    record["t0_s"] = record["t1_s"]
    with pytest.raises(LegacyCoordinateError):
        adapter.adapt(record)


def test_time_bbox_exceeding_recording_rejected(adapter):
    record = _valid_record()
    record["t1_s"] = 0.151
    with pytest.raises(LegacyCoordinateError):
        adapter.adapt(record)


def test_invalid_frequency_bbox_rejected(adapter):
    record = _valid_record()
    record["f0_hz"] = record["f1_hz"]
    with pytest.raises(LegacyCoordinateError):
        adapter.adapt(record)


def test_frequency_bbox_outside_recording_rejected(adapter):
    record = _valid_record()
    record["f0_hz"] = 2430.0e6 - 1.0
    with pytest.raises(LegacyCoordinateError):
        adapter.adapt(record)


def test_missing_final_confidence_rejected_not_fabricated(adapter):
    record = _valid_record()
    del record["score"]
    with pytest.raises(LegacyRecordError):
        adapter.adapt(record)
    # Ensure the failure is specifically the missing-confidence guard.
    with pytest.raises(LegacyConfidenceMissing):
        adapter.adapt(_missing_conf(record))


def _missing_conf(record: dict) -> dict:
    import copy

    value = copy.deepcopy(record)
    value.pop("score", None)
    return value


def test_confidence_out_of_range_rejected(adapter):
    record = _valid_record()
    record["score"] = 1.5
    with pytest.raises(LegacyConfidenceMissing):
        adapter.adapt(record)


def test_confidence_is_preserved_not_fabricated(adapter):
    record = _valid_record()
    record["score"] = 0.949
    assert adapter.adapt(record).confidence == 0.949


def test_sample_identity_mismatch_rejected(adapter):
    record = _valid_record()
    record["sample_id"] = "1"
    with pytest.raises(LegacySampleIdentityMismatch):
        adapter.adapt(record)


def test_malformed_record_rejected(adapter):
    with pytest.raises(LegacyRecordError):
        adapter.adapt({"sample_id": "0"})


def test_adapt_many_preserves_order(adapter):
    first = _valid_record()
    second = _valid_record()
    second["t0_s"] = 0.02
    second["t1_s"] = 0.03
    results = adapter.adapt_many([first, second])
    assert len(results) == 2
    assert results[0].t_start_s == 0.005
    assert results[1].t_start_s == 0.02
"""B2: SpaceNet sidecar metadata on standalone uploads.

Uploading an IQ file together with its SpaceNet ``.json`` sidecar derives the
sampling rate, center frequency, label space, and ground truth from the verified
dataset contract, instead of trusting hand-typed values. Explicit form values
still win. A non-SpaceNet document is rejected rather than silently ignored.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.recordings.spacenet_upload import (
    SPACENET_UPLOAD_LABEL_SPACE,
    parse_upload_metadata,
)

LABEL_SPACE_ROOT = Path(__file__).resolve().parents[2] / "label_spaces"

# 11.json: observation_range 2409-2459 MHz (bandwidth 50 MHz), GT spans 5-40 ms.
SPACENET_DOCUMENT = json.dumps(
    {
        "observation_range": [2409.0, 2459.0],
        "signals": [
            {
                "signal_id": 0,
                "start_frequency": 2419.94,
                "end_frequency": 2420.06,
                "start_time": 18.0,
                "end_time": 40.0,
                "class": 13,
            },
            {
                "signal_id": 1,
                "start_frequency": 2447.0,
                "end_frequency": 2457.0,
                "start_time": 29.0,
                "end_time": 40.0,
                "class": 11,
            },
        ],
    }
)
# 2,000,000 float16 I/Q samples -> 8,000,000 bytes -> 0.04 s at 50 MHz.
SPACENET_BIN_BYTES = 8_000_000
SPACENET_COMPLEX64_BYTES = 16_000_000


def test_parse_space_net_sidecar_derives_rate_center_and_ground_truth():
    metadata = parse_upload_metadata(
        SPACENET_DOCUMENT,
        label_space_root=LABEL_SPACE_ROOT,
        num_samples=2_000_000,
    )

    # observation_range 2409-2459 MHz -> 50 MHz bandwidth, 2434 MHz midpoint.
    assert metadata.sample_rate_hz == 50_000_000.0
    assert metadata.center_frequency_hz == 2_434_000_000.0
    assert metadata.label_space == SPACENET_UPLOAD_LABEL_SPACE
    assert len(metadata.ground_truth) == 2
    first = metadata.ground_truth[0]
    assert first.class_id == 13
    assert first.class_name == "FM"
    assert first.t_start_s == 0.018
    assert first.f_low_hz == 2_419_940_000.0


def test_ground_truth_outside_iq_duration_is_rejected():
    from app.core.errors import PlatformError

    # 4,000 float16 samples -> 80 us, far shorter than the 5-40 ms ground truth.
    try:
        parse_upload_metadata(
            SPACENET_DOCUMENT, label_space_root=LABEL_SPACE_ROOT, num_samples=4_000
        )
    except PlatformError as error:
        assert error.code == "INVALID_SPACENET_SAMPLE"
    else:  # pragma: no cover - fail closed
        raise AssertionError("out-of-range ground truth must be rejected")


def test_missing_sidecar_yields_no_derived_values():
    metadata = parse_upload_metadata(None, label_space_root=LABEL_SPACE_ROOT)
    assert metadata.sample_rate_hz is None
    assert metadata.center_frequency_hz is None
    assert metadata.label_space is None
    assert metadata.ground_truth == ()


def test_unrelated_json_document_is_rejected():
    from app.core.errors import PlatformError

    try:
        parse_upload_metadata('{"foo": 1}', label_space_root=LABEL_SPACE_ROOT)
    except PlatformError as error:
        assert error.code == "INVALID_RECORDING_METADATA"
    else:  # pragma: no cover - fail closed
        raise AssertionError("non-SpaceNet metadata must be rejected")


def _upload(client, *, data: bytes, metadata: str | None, fields: dict[str, str]):
    files = {"file": ("sample.bin", data, "application/octet-stream")}
    if metadata is not None:
        files["metadata"] = ("sample.json", metadata, "application/json")
    return client.post("/api/recordings", data=fields, files=files)


def test_upload_with_sidecar_derives_metadata_and_ground_truth(client):
    response = _upload(
        client,
        data=b"\x00" * SPACENET_COMPLEX64_BYTES,
        metadata=SPACENET_DOCUMENT,
        fields={"name": "spacenet-upload"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sample_rate_hz"] == 50_000_000.0
    assert body["center_frequency_hz"] == 2_434_000_000.0
    assert body["frequency_low_hz"] == 2_409_000_000.0
    assert body["frequency_high_hz"] == 2_459_000_000.0
    assert body["num_samples"] == 2_000_000
    assert body["duration_s"] == 0.04
    assert body["label_space"] == SPACENET_UPLOAD_LABEL_SPACE
    assert body["has_ground_truth"] is True

    ground_truth = client.get(f"/api/recordings/{body['id']}/ground-truth").json()
    assert len(ground_truth) == 2
    assert ground_truth[0]["class_name"] == "FM"


def test_explicit_form_values_override_the_sidecar(client):
    response = _upload(
        client,
        data=b"\x00" * SPACENET_COMPLEX64_BYTES,
        metadata=SPACENET_DOCUMENT,
        fields={
            "name": "override",
            "sample_rate_hz": "50000000",
            "center_frequency_hz": "2400000000",
            "label_space": "signal_presence_v1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sample_rate_hz"] == 50_000_000.0
    assert body["center_frequency_hz"] == 2_400_000_000.0
    assert body["frequency_low_hz"] == 2_375_000_000.0
    assert body["label_space"] == "signal_presence_v1"


def test_upload_without_sidecar_or_rate_fails_closed(client):
    response = _upload(
        client, data=b"\x00" * SPACENET_COMPLEX64_BYTES, metadata=None, fields={"name": "no-rate"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_RECORDING"

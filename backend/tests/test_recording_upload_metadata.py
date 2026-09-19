"""B2: SpaceNet sidecar metadata on standalone uploads.

Uploading an IQ file together with its SpaceNet ``.json`` sidecar derives the
sampling rate, center frequency, label space, and ground truth from the verified
dataset contract, instead of trusting hand-typed values. Explicit form values
still win. A non-SpaceNet document is rejected rather than silently ignored.

A SpaceNet sidecar also pins the IQ encoding: SpaceNet ships little-endian
float16 I/Q pairs, so a raw ``.bin`` must NOT be read as ``complex64_le``
(which would halve the derived duration and mis-align every sample).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.recordings.spacenet_upload import (
    SPACENET_UPLOAD_DATA_FORMAT,
    SPACENET_UPLOAD_LABEL_SPACE,
    parse_upload_metadata,
)

LABEL_SPACE_ROOT = Path(__file__).resolve().parents[2] / "label_spaces"

# 5.json: observation_range 2443-2473 MHz (bandwidth 30 MHz), GT spans 17-40 ms.
SPACENET_DOCUMENT = json.dumps(
    {
        "observation_range": [2443.0, 2473.0],
        "signals": [
            {
                "signal_id": 0,
                "start_frequency": 2446.0,
                "end_frequency": 2448.0,
                "start_time": 19.0,
                "end_time": 40.0,
                "class": 8,
            },
            {
                "signal_id": 1,
                "start_frequency": 2449.5,
                "end_frequency": 2450.5,
                "start_time": 17.0,
                "end_time": 30.0,
                "class": 6,
            },
        ],
    }
)
# 4 bytes/sample * 1,200,000 float16 I/Q samples -> 4,800,000 bytes -> 0.04 s at 30 MHz.
SPACENET_FLOAT16_BYTES = 4_800_000
SPACENET_BIN_STEM = "5"


def _upload(client, *, data: bytes, metadata: str | None, fields: dict[str, str]):
    files = {"file": ("sample.bin", data, "application/octet-stream")}
    if metadata is not None:
        files["metadata"] = ("sample.json", metadata, "application/json")
    return client.post("/api/recordings", data=fields, files=files)


def test_parse_space_net_sidecar_derives_rate_center_and_ground_truth():
    metadata = parse_upload_metadata(
        SPACENET_DOCUMENT,
        label_space_root=LABEL_SPACE_ROOT,
        num_samples=1_200_000,
    )

    # observation_range 2443-2473 MHz -> 30 MHz bandwidth, 2458 MHz midpoint.
    assert metadata.sample_rate_hz == 30_000_000.0
    assert metadata.center_frequency_hz == 2_458_000_000.0
    assert metadata.label_space == SPACENET_UPLOAD_LABEL_SPACE
    assert len(metadata.ground_truth) == 2
    first = metadata.ground_truth[0]
    assert first.class_id == 8
    assert first.class_name == "Zigbee"
    assert first.t_start_s == 0.019
    assert first.f_low_hz == 2_446_000_000.0


def test_ground_truth_outside_iq_duration_is_rejected():
    from app.core.errors import PlatformError

    # 4,000 float16 samples -> 133 us, far shorter than the 17-40 ms ground truth.
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


def test_uploaded_space_net_bin_is_read_as_float16(client):
    """A raw SpaceNet .bin must not be misread as complex64 (halved duration)."""
    response = _upload(
        client,
        data=b"\x00" * SPACENET_FLOAT16_BYTES,
        metadata=SPACENET_DOCUMENT,
        fields={"name": SPACENET_BIN_STEM},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data_format"] == SPACENET_UPLOAD_DATA_FORMAT
    # 4,800,000 bytes / 4 = 1,200,000 float16 samples -> 0.04 s at 30 MHz.
    assert body["num_samples"] == 1_200_000
    assert body["duration_s"] == 0.04
    assert body["sample_rate_hz"] == 30_000_000.0
    assert body["center_frequency_hz"] == 2_458_000_000.0
    assert body["frequency_low_hz"] == 2_443_000_000.0
    assert body["frequency_high_hz"] == 2_473_000_000.0
    assert body["label_space"] == SPACENET_UPLOAD_LABEL_SPACE
    assert body["has_ground_truth"] is True
    ground_truth = client.get(f"/api/recordings/{body['id']}/ground-truth").json()
    assert len(ground_truth) == 2
    # Deterministic physical order: earlier start time first (17 ms before 19 ms).
    assert [row["class_id"] for row in ground_truth] == [6, 8]
    assert [row["class_name"] for row in ground_truth] == ["BLE LE1M", "Zigbee"]


def test_explicit_form_values_override_the_sidecar(client):
    # An explicit complex64_le format reads the same bytes as half as many samples,
    # so give it 2x the bytes to keep the 17-40 ms ground truth inside 0.04 s.
    response = _upload(
        client,
        data=b"\x00" * (SPACENET_FLOAT16_BYTES * 2),
        metadata=SPACENET_DOCUMENT,
        fields={
            "name": "override",
            "data_format": "complex64_le",
            "sample_rate_hz": "30000000",
            "center_frequency_hz": "2400000000",
            "label_space": "signal_presence_v1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data_format"] == "complex64_le"
    assert body["center_frequency_hz"] == 2_400_000_000.0
    assert body["frequency_low_hz"] == 2_385_000_000.0
    assert body["label_space"] == "signal_presence_v1"


def test_upload_without_sidecar_or_rate_fails_closed(client):
    response = _upload(
        client, data=b"\x00" * 16_000, metadata=None, fields={"name": "no-rate"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_RECORDING"

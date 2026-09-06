import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.remote_execution.schema import (
    RemoteBatchStatusV1,
    RemoteExecutionBatchV1,
    RemoteExecutionEnvelopeV1,
    RemoteExecutionItemV1,
    RemoteExecutionRequestV1,
    RemoteItemStatusV1,
    RemoteRecordingRefV1,
    parse_remote_execution_batch_json,
)


def _recording(**overrides):
    values = dict(
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        expected_recording_fingerprint="a" * 64,
        expected_source_data_sha256="b" * 64,
    )
    values.update(overrides)
    return RemoteRecordingRefV1(**values)


def _item(item_key="000000", local_run_id="run_a", **overrides):
    values = dict(
        item_key=item_key,
        request_id=f"req_{item_key}",
        local_run_id=local_run_id,
        orchestrator_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        recording=_recording(),
        parameters={},
    )
    values.update(overrides)
    return RemoteExecutionItemV1(**values)


def _batch(**overrides):
    values = dict(
        schema_version=1,
        batch_id="batch_x",
        required_remote_runtime_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        pipeline={"id": "zoomspec_yolo26n_aug_combined_frn_v3", "version": "1.0.0"},
        asset_manifest_sha256="c" * 64,
        items=[_item()],
        request_sha256="d" * 64,
    )
    values.update(overrides)
    return RemoteExecutionBatchV1(**values)


def test_valid_recording_ref_parses():
    recording = _recording()
    assert recording.dataset_name == "SpaceNet"
    assert recording.expected_recording_fingerprint == "a" * 64
    assert recording.expected_source_data_sha256 == "b" * 64


@pytest.mark.parametrize("bad", [
    "A" * 64,           # uppercase
    "a" * 63,           # wrong length
    "g" + "a" * 63,     # non-hex
])
def test_invalid_sha256_rejected(bad):
    with pytest.raises(ValidationError):
        _recording(expected_recording_fingerprint=bad)


@pytest.mark.parametrize("bad", [
    "a" * 41,           # wrong length
    "a" * 39,           # wrong length
    "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7G",  # non-hex
])
def test_invalid_git_commit_rejected(bad):
    with pytest.raises(ValidationError):
        _batch(required_remote_runtime_commit=bad)


def test_non_v1_schema_version_rejected():
    with pytest.raises(ValidationError):
        _batch(schema_version=2)


def test_extra_top_level_field_rejected():
    with pytest.raises(ValidationError):
        _batch(extra_field="nope")


def test_extra_nested_pipeline_field_rejected():
    with pytest.raises(ValidationError):
        _batch(pipeline={"id": "pipeline_x", "version": "1.0.0", "extra": True})


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        RemoteItemStatusV1(item_key="000000", status="definitely_not_a_status")
    with pytest.raises(ValidationError):
        RemoteBatchStatusV1(batch_id="batch_x", status="paused", items=[])


def test_batch_zero_items_rejected():
    with pytest.raises(ValidationError):
        _batch(items=[])


def test_duplicate_item_key_rejected():
    with pytest.raises(ValidationError):
        _batch(items=[
            _item(item_key="000000"),
            _item(item_key="000000", local_run_id="run_b", request_id="req_2"),
        ])


def test_duplicate_local_run_id_rejected():
    with pytest.raises(ValidationError):
        _batch(items=[
            _item(local_run_id="run_a"),
            _item(item_key="000001", local_run_id="run_a", request_id="req_2"),
        ])


def test_duplicate_request_id_rejected():
    with pytest.raises(ValidationError):
        _batch(items=[
            _item(request_id="req_x"),
            _item(item_key="000001", local_run_id="run_b", request_id="req_x"),
        ])


def test_schema_version_string_not_coerced():
    with pytest.raises(ValidationError):
        _batch(schema_version="1")


def test_valid_request_parses():
    request = RemoteExecutionRequestV1(
        schema_version=1,
        request_id="req_000000",
        local_run_id="run_a",
        orchestrator_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        required_remote_runtime_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        pipeline={"id": "zoomspec_yolo26n_aug_combined_frn_v3", "version": "1.0.0"},
        recording=_recording(),
        parameters={},
        asset_manifest_sha256="c" * 64,
    )
    assert request.request_id == "req_000000"
    assert request.local_run_id == "run_a"
    assert request.orchestrator_commit == "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"


def test_valid_envelope_parses():
    envelope = RemoteExecutionEnvelopeV1(
        schema_version=1,
        request_id="req_000000",
        batch_id="batch_x",
        item_key="000000",
        local_run_id="run_a",
        recording_fingerprint="a" * 64,
        source_data_sha256="b" * 64,
        pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
        pipeline_version="1.0.0",
        orchestrator_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        remote_runtime_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        asset_manifest_sha256="c" * 64,
        hardware={"gpu": "RTX 4090"},
        payload_sha256="d" * 64,
        remote_started_at=datetime(2026, 1, 1, 0, 0, 0),
        remote_finished_at=None,
    )
    assert envelope.pipeline_id == "zoomspec_yolo26n_aug_combined_frn_v3"
    assert envelope.payload_sha256 == "d" * 64
    assert envelope.remote_started_at == datetime(2026, 1, 1, 0, 0, 0)


def test_valid_batch_status_parses():
    status = RemoteBatchStatusV1(
        batch_id="batch_x",
        status="running",
        items=[RemoteItemStatusV1(item_key="000000", status="queued")],
    )
    assert status.status == "running"
    assert status.items[0].result_relative_path is None


@pytest.mark.parametrize("good", [
    "SpaceNet",
    "test",
    "0",
    "000000",
    "req_000000",
    "run_a",
    "batch_x",
    "spacenet_14",
    "zoomspec_yolo26n_aug_combined_frn_v3",
    "1.0.0",
])
def test_safe_wire_identifier_accepted(good):
    recording = _recording(dataset_name=good, dataset_split=good, dataset_key=good)
    batch = _batch(batch_id=good, pipeline={"id": good, "version": "1.0.0"})
    assert batch.batch_id == good
    assert recording.dataset_key == good


@pytest.mark.parametrize("bad", [
    "../escape",
    "./x",
    "/x",
    "a/b",
    r"a\b",
    "C:\\tmp",
    "abc def",
    " leading",
    "trailing ",
    "",
])
def test_unsafe_wire_identifier_rejected(bad):
    with pytest.raises(ValidationError):
        _batch(batch_id=bad)
    with pytest.raises(ValidationError):
        _item(item_key=bad)
    with pytest.raises(ValidationError):
        _recording(dataset_key=bad)


def test_valid_relative_result_path_accepted():
    status = RemoteItemStatusV1(
        item_key="000000", status="completed",
        result_relative_path="results/000000/analysis_result.zip",
    )
    assert status.result_relative_path == "results/000000/analysis_result.zip"


@pytest.mark.parametrize("bad", [
    "/tmp/a.zip",
    r"\absolute",
    r"C:\tmp\a.zip",
    "C:/tmp/a.zip",
    "../a.zip",
    "results/../a.zip",
    r"results\..\a.zip",
    r".\result.zip",
    "./result.zip",
    "result.zip\x00junk",
    " result.zip",
    "result.zip ",
])
def test_unsafe_result_path_rejected(bad):
    with pytest.raises(ValidationError):
        RemoteItemStatusV1(item_key="000000", status="completed", result_relative_path=bad)


def test_parse_remote_execution_batch_json_round_trips():
    batch = _batch()
    raw = batch.model_dump_json().encode("utf-8")
    parsed = parse_remote_execution_batch_json(raw)
    assert isinstance(parsed, RemoteExecutionBatchV1)
    assert parsed.batch_id == batch.batch_id
    assert parsed.request_sha256 == batch.request_sha256
    assert parsed.items[0].item_key == "000000"


def test_parse_duplicate_top_level_key_rejected():
    batch = _batch()
    data = batch.model_dump()
    text = json.dumps(
        {key: value for key, value in data.items() if key != "batch_id"},
        separators=(",", ":"),
    )
    raw = (text[:-1] + ',"batch_id":"first","batch_id":"second"}').encode("utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_remote_execution_batch_json(raw)


def test_parse_duplicate_nested_parameter_key_rejected():
    batch = _batch()
    text = json.dumps(batch.model_dump(), separators=(",", ":"))
    raw = text.replace('"parameters":{}', '"parameters":{"dup":1,"dup":2}').encode("utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_remote_execution_batch_json(raw)


def test_parse_non_finite_json_constant_rejected():
    batch = _batch()
    data = batch.model_dump()
    data["items"][0]["parameters"] = {"x": float("nan")}
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    assert "NaN" in raw.decode("utf-8")
    with pytest.raises(ValueError):
        parse_remote_execution_batch_json(raw)


def test_parse_invalid_utf8_bytes_rejected():
    with pytest.raises(ValueError):
        parse_remote_execution_batch_json(b'{"schema_version":1,"batch_id":"\xff"}')


def test_parse_top_level_must_be_object():
    with pytest.raises(ValueError):
        parse_remote_execution_batch_json(b"[1,2,3]")


def test_parse_extra_field_rejected():
    batch = _batch()
    data = batch.model_dump()
    data["extra_field"] = "nope"
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    with pytest.raises(ValidationError):
        parse_remote_execution_batch_json(raw)


def test_parse_schema_version_string_rejected():
    batch = _batch()
    data = batch.model_dump()
    data["schema_version"] = "1"
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    with pytest.raises(ValidationError):
        parse_remote_execution_batch_json(raw)
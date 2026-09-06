import json
from datetime import datetime
from pathlib import Path
import zipfile

import pytest

from benchmark_fixture import add_detection, add_recording

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.labels.service import LabelSpaceService
from app.pipelines.base import PipelineDefinition
from app.recordings.model import RecordingModel
from app.remote_execution.result_ingestor import (
    ingest_remote_result,
    parse_remote_execution_envelope_json,
)
from app.remote_execution.schema import RemoteExecutionEnvelopeV1
from app.remote_execution.source_hash import compute_file_sha256
from app.remote_execution.validation import AnalysisResultWriter

ORCHESTRATOR_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"


class SpyWriter(AnalysisResultWriter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.persist_calls = 0

    def persist(self, *args, **kwargs):
        self.persist_calls += 1
        return super().persist(*args, **kwargs)


def _definition():
    return PipelineDefinition(
        id="pipeline_x",
        name="Pipeline X",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
    )


def _seed_recording(session):
    add_recording(session, recording_id="rec_r", name="r",
                  dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14")
    recording = session.get(RecordingModel, "rec_r")
    recording.source_data_sha256 = "b" * 64
    session.commit()
    return recording


def _seed_remote_run(session, *, status="running", payload_sha256=None, missing_key=None, executor="remote_gpu"):
    _seed_recording(session)
    metadata = {
        "request_id": "req_1",
        "batch_id": "batch_x",
        "item_key": "000000",
        "recording_fingerprint": "a" * 64,
        "source_data_sha256": "b" * 64,
        "orchestrator_commit": ORCHESTRATOR_COMMIT,
        "required_remote_runtime_commit": ORCHESTRATOR_COMMIT,
        "asset_manifest_sha256": "c" * 64,
    }
    if payload_sha256 is not None:
        metadata = dict(metadata)
        metadata["payload_sha256"] = payload_sha256
    if missing_key is not None:
        metadata.pop(missing_key, None)
    run = AnalysisRunModel(
        id="run_r",
        recording_id="rec_r",
        pipeline_id="pipeline_x",
        pipeline_version="1.0",
        executor=executor,
        status=status,
        parameters_json={},
        execution_metadata_json=metadata,
    )
    session.add(run)
    session.commit()
    return run


def _envelope(payload_sha256, **overrides):
    values = dict(
        schema_version=1,
        request_id="req_1",
        batch_id="batch_x",
        item_key="000000",
        local_run_id="run_r",
        recording_fingerprint="a" * 64,
        source_data_sha256="b" * 64,
        pipeline_id="pipeline_x",
        pipeline_version="1.0",
        orchestrator_commit=ORCHESTRATOR_COMMIT,
        remote_runtime_commit=ORCHESTRATOR_COMMIT,
        asset_manifest_sha256="c" * 64,
        hardware={"gpu": "RTX 4090"},
        payload_sha256=payload_sha256,
        remote_started_at=datetime(2026, 1, 1, 0, 0, 0),
        remote_finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )
    values.update(overrides)
    return RemoteExecutionEnvelopeV1(**values)


def _apply_overrides(base, overrides):
    if overrides is None:
        return base
    result = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def _write_analysis_result_zip(tmp_path, *, manifest_overrides=None, detection_overrides=None, traversal=False):
    manifest = _apply_overrides({
        "schema_version": 1,
        "pipeline": {"id": "pipeline_x", "name": "Pipeline X", "version": "1.0"},
        "label_space": "spacenet_14",
        "recording": {"name": "r", "dataset": "SpaceNet"},
        "execution": {"executor": "remote_gpu", "device": "GPU", "environment": "autodl"},
        "results": {"detections": "detections.json"},
        "parameters": {},
    }, manifest_overrides)
    detections = [{
        "id": "source_det_1",
        "t_start_s": 0.01,
        "t_end_s": 0.02,
        "f_low_hz": 2440600000.0,
        "f_high_hz": 2440700000.0,
        "class_id": 9,
        "class_name": "LoRa 250kHz",
        "confidence": 0.94,
        "scores": {"classification": 0.94},
    }]
    if detection_overrides:
        detections[0].update(detection_overrides)
    zip_path = tmp_path / "analysis_result.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("detections.json", json.dumps({"detections": detections}))
        if traversal:
            archive.writestr("../escape.bin", b"escape")
    payload = compute_file_sha256(zip_path)
    return zip_path, payload


def _writer(session, settings, workspace):
    return SpyWriter(
        session=session,
        label_service=LabelSpaceService(settings.label_space_root),
        pipeline_definition=_definition(),
        workspace=workspace,
    )


def _detections(session, run_id="run_r"):
    return session.query(DetectionResultModel).filter(DetectionResultModel.run_id == run_id).all()


def test_fresh_running_ingest(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    run = _seed_remote_run(session, status="running")
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    result = ingest_remote_result(session, "run_r", envelope, zip_path, writer)

    assert result == payload
    assert writer.persist_calls == 1
    assert run.status == "completed"
    assert run.finished_at is not None

    detections = _detections(session)
    assert len(detections) == 1
    det = detections[0]
    assert det.id.startswith("det_")
    assert det.id != "source_det_1"
    assert det.class_id == 9
    assert det.class_name == "LoRa 250kHz"
    assert det.confidence == 0.94
    assert det.scores_json == {"classification": 0.94}

    metadata = dict(run.execution_metadata_json)
    assert metadata["payload_sha256"] == payload
    assert metadata["remote_runtime_commit"] == envelope.remote_runtime_commit
    assert metadata["remote_started_at"] == "2026-01-01T00:00:00"
    assert metadata["remote_finished_at"] == "2026-01-01T00:01:00"
    assert run.hardware_info_json == envelope.hardware

    session.commit()
    session.expire_all()
    reloaded = session.get(AnalysisRunModel, "run_r")
    assert reloaded.status == "completed"
    assert reloaded.execution_metadata_json["payload_sha256"] == payload
    assert len(_detections(session)) == 1


def test_completed_same_payload_is_pure_no_op(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="completed", payload_sha256=payload)
    add_detection(session, detection_id="det_old", run_id="run_r", class_id=6, class_name="BLE LE1M",
                  confidence=0.5, t0=0.01, t1=0.02, f0=2440600000.0, f1=2440700000.0)
    session.commit()
    metadata_before = dict(run.execution_metadata_json)
    finished_before = run.finished_at
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    result = ingest_remote_result(session, "run_r", envelope, zip_path, writer)

    assert result == payload
    assert writer.persist_calls == 0
    assert [d.id for d in _detections(session)] == ["det_old"]
    assert run.status == "completed"
    assert run.finished_at == finished_before
    assert run.execution_metadata_json == metadata_before


def test_completed_different_payload_conflicts(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="completed", payload_sha256="f" * 64)
    add_detection(session, detection_id="det_old", run_id="run_r", class_id=6, class_name="BLE LE1M",
                  confidence=0.5, t0=0.01, t1=0.02, f0=2440600000.0, f1=2440700000.0)
    session.commit()
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_CONFLICT"

    session.rollback()
    assert [d.id for d in _detections(session)] == ["det_old"]


@pytest.mark.parametrize("terminal", ["failed", "interrupted"])
def test_terminal_cannot_be_resurrected(session, tmp_path, settings, terminal):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status=terminal)
    add_detection(session, detection_id="det_old", run_id="run_r", class_id=6, class_name="BLE LE1M",
                  confidence=0.5, t0=0.01, t1=0.02, f0=2440600000.0, f1=2440700000.0)
    session.commit()
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert run.status == terminal
    assert writer.persist_calls == 0


def test_payload_sha_mismatch(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, _ = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="running")
    envelope = _envelope("d" * 64)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert writer.persist_calls == 0
    assert _detections(session) == []
    assert run.status == "running"


def test_traversal_archive_rejected(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path, traversal=True)
    run = _seed_remote_run(session, status="running")
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_INVALID"

    session.rollback()
    assert run.status == "running"
    assert _detections(session) == []


@pytest.mark.parametrize("executor", ["local_cpu", "imported"])
def test_local_executor_protected(session, tmp_path, settings, executor):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="running", executor=executor)
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert writer.persist_calls == 0


@pytest.mark.parametrize("override", [
    {"request_id": "req_9"},
    {"batch_id": "batch_y"},
    {"item_key": "111111"},
    {"local_run_id": "run_other"},
    {"recording_fingerprint": "e" * 64},
    {"source_data_sha256": "f" * 64},
    {"orchestrator_commit": "a" * 40},
    {"remote_runtime_commit": "a" * 40},
    {"asset_manifest_sha256": "e" * 64},
    {"pipeline_id": "wrong_pipeline"},
    {"pipeline_version": "9.9"},
])
def test_envelope_identity_mismatch(session, tmp_path, settings, override):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="running")
    envelope = _envelope(payload, **override)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert writer.persist_calls == 0


@pytest.mark.parametrize("missing_key", [
    "request_id", "batch_id", "item_key", "recording_fingerprint",
    "source_data_sha256", "orchestrator_commit",
    "required_remote_runtime_commit", "asset_manifest_sha256",
])
def test_missing_expected_provenance(session, tmp_path, settings, missing_key):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="running", missing_key=missing_key)
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert writer.persist_calls == 0


@pytest.mark.parametrize("manifest_override", [
    {"pipeline": {"id": "wrong_pipeline"}},
    {"pipeline": {"version": "9.9"}},
    {"execution": {"executor": "local_cpu"}},
    {"recording": {"name": "wrong_name"}},
    {"recording": {"dataset": "WrongSet"}},
])
def test_manifest_consistency_mismatch(session, tmp_path, settings, manifest_override):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path, manifest_overrides=manifest_override)
    run = _seed_remote_run(session, status="running")
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert writer.persist_calls == 0


def test_confidence_validity_reused(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path, detection_overrides={"confidence": 1.5})
    run = _seed_remote_run(session, status="running")
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    with pytest.raises(PlatformError) as exc:
        ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert exc.value.code == "REMOTE_RESULT_INVALID"
    assert writer.persist_calls == 0


def test_parse_envelope_valid():
    payload = "d" * 64
    raw = _envelope(payload, remote_started_at=None, remote_finished_at=None).model_dump_json()
    parsed = parse_remote_execution_envelope_json(raw)
    assert isinstance(parsed, RemoteExecutionEnvelopeV1)
    assert parsed.payload_sha256 == payload
    assert parsed.local_run_id == "run_r"


def test_parse_envelope_duplicate_top_level_key_rejected():
    envelope = _envelope("d" * 64, remote_started_at=None, remote_finished_at=None)
    data = envelope.model_dump()
    text = json.dumps({k: v for k, v in data.items() if k != "request_id"}, separators=(",", ":"))
    raw = text[:-1] + ',"request_id":"req_1","request_id":"req_2"}'
    with pytest.raises(PlatformError) as exc:
        parse_remote_execution_envelope_json(raw)
    assert exc.value.code == "REMOTE_RESULT_INVALID"


def test_parse_envelope_duplicate_nested_key_rejected():
    envelope = _envelope("d" * 64, remote_started_at=None, remote_finished_at=None)
    data = envelope.model_dump()
    text = json.dumps(data, separators=(",", ":"))
    raw = text.replace('"hardware":{"gpu":"RTX 4090"}', '"hardware":{"gpu":"a","gpu":"b"}')
    with pytest.raises(PlatformError) as exc:
        parse_remote_execution_envelope_json(raw)
    assert exc.value.code == "REMOTE_RESULT_INVALID"


@pytest.mark.parametrize("bad", [
    '{"schema_version":1,"request_id":"r","hardware":{"x":NaN}}',
    '{"schema_version":1,"request_id":"r","hardware":{"x":Infinity}}',
    '{"schema_version":1,"request_id":"r","hardware":{"x":-Infinity}}',
])
def test_parse_envelope_non_finite_rejected(bad):
    with pytest.raises(PlatformError) as exc:
        parse_remote_execution_envelope_json(bad)
    assert exc.value.code == "REMOTE_RESULT_INVALID"


def test_parse_envelope_top_level_array_rejected():
    with pytest.raises(PlatformError) as exc:
        parse_remote_execution_envelope_json(b"[1,2,3]")
    assert exc.value.code == "REMOTE_RESULT_INVALID"


def test_parse_envelope_extra_field_rejected():
    envelope = _envelope("d" * 64, remote_started_at=None, remote_finished_at=None)
    data = envelope.model_dump()
    data["extra_field"] = "x"
    raw = json.dumps(data, separators=(",", ":"))
    with pytest.raises(PlatformError) as exc:
        parse_remote_execution_envelope_json(raw)
    assert exc.value.code == "REMOTE_RESULT_INVALID"


def test_ingestor_owns_no_transaction(session, tmp_path, settings, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path, payload = _write_analysis_result_zip(tmp_path)
    run = _seed_remote_run(session, status="running")
    envelope = _envelope(payload)
    writer = _writer(session, settings, workspace)

    original_commit = session.commit
    original_rollback = session.rollback

    def _block_commit():
        raise AssertionError("ingestor must not commit")

    def _block_rollback():
        raise AssertionError("ingestor must not rollback")

    monkeypatch.setattr(session, "commit", _block_commit)
    monkeypatch.setattr(session, "rollback", _block_rollback)

    result = ingest_remote_result(session, "run_r", envelope, zip_path, writer)
    assert result == payload
    assert run.status == "completed"
    original_rollback()
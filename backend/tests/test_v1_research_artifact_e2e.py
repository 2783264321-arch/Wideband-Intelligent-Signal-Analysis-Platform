"""Plan 3 Stage A: V1 research artifact no-GPU acceptance.

Proves: exporter -> BAPv1 ZIP -> external archive SHA256 -> Windows import ->
exact identity resolution -> atomic runs/detections -> idempotent re-import ->
inconsistent-state fail-closed -> evaluation readiness, with no remote_gpu and
no platform.db synchronization.

GPU REQUIRED: NO. No model, no CUDA, no torch/ultralytics, no SSH.
"""
from __future__ import annotations

from io import BytesIO
import importlib.util
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

from app.imported_runs.batch_schema import BatchManifest

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_SPACE_ROOT = REPO_ROOT / "label_spaces"
LABEL_SPACE_PATH = LABEL_SPACE_ROOT / "spacenet_14.json"


def _write_sample(split_dir: Path, name: str, *, num_samples: int = 6000) -> None:
    interleaved = np.zeros(num_samples * 2, dtype="<f2")
    interleaved.tofile(split_dir / f"{name}.bin")
    (split_dir / f"{name}.json").write_text(
        json.dumps({
            "observation_range": [2401.0, 2431.0],
            "signals": [{
                "signal_id": 0, "start_frequency": 2417.0, "end_frequency": 2417.1,
                "start_time": 0.02, "end_time": 0.1, "class": 9,
            }],
        }),
        encoding="utf-8",
    )


def _dataset(tmp_path: Path, names=("0", "1")) -> Path:
    split = tmp_path / "advanced" / "test"
    split.mkdir(parents=True, exist_ok=True)
    for name in names:
        _write_sample(split, name)
    return split


def _predictions(tmp_path: Path, rows) -> Path:
    path = tmp_path / "preds.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _det_row(sample_id, *, t0=2e-5, t1=1e-4):
    return {"sample_id": sample_id, "t0_s": t0, "t1_s": t1,
            "f0_hz": 2417.0e6, "f1_hz": 2417.1e6, "class_id": 9, "score": 0.9}


def _build_batch(tmp_path: Path, *, sample_ids=("0", "1")):
    from research.m9_legacy_bridge.adapter import load_label_space
    from research.v1_artifact_exporter.exporter import ResearchBatchRequest, export_research_batch
    from research.v1_artifact_exporter.predictions import load_predictions_jsonl
    from research.v1_artifact_exporter.sources import build_research_samples
    from app.imported_runs.batch_schema import ResultProvenance, TransportProvenance
    from app.imported_runs.schema import ExecutionMetadata, PipelineMetadata

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0"), _det_row("1")])
    samples = build_research_samples(
        dataset_dir=split,
        label_space_root=LABEL_SPACE_ROOT,
        label_space_id="spacenet_14",
        label_classes=load_label_space(LABEL_SPACE_PATH),
        sample_ids=list(sample_ids),
        predictions_by_sample=load_predictions_jsonl(preds),
        dataset_name="SpaceNet",
        dataset_split="test",
    )
    request = ResearchBatchRequest(
        dataset_name="SpaceNet",
        dataset_split="test",
        label_space="spacenet_14",
        pipeline=PipelineMetadata(id="pipeline_x", name="Pipeline X", version="1.0"),
        execution=ExecutionMetadata(executor="research_gpu", device="cuda:0", environment="test"),
        result_provenance=ResultProvenance(source_predictions_sha256="a" * 64),
        transport_provenance=TransportProvenance(
            exporter_version="research_batch_exporter_v1",
            export_timestamp="2026-09-16T00:00:00Z",
        ),
        batch_id="batch_e2e",
        samples=samples,
        output_path=tmp_path / "batch.zip",
    )
    return export_research_batch(request), samples


def _seed_recordings(client, samples) -> None:
    from app.recordings.model import RecordingModel
    from app.ground_truth.model import GroundTruthModel

    with client.app.state.database.session_factory() as session:
        for sample in samples:
            mr = sample.manifest_recording
            session.add(RecordingModel(
                id=f"rec_{mr.name}", name=mr.name, data_path=f"x/{mr.name}.bin",
                data_format=mr.data_format, sample_rate_hz=mr.sample_rate_hz,
                center_frequency_hz=mr.center_frequency_hz, frequency_low_hz=mr.frequency_low_hz,
                frequency_high_hz=mr.frequency_high_hz, num_samples=mr.num_samples,
                duration_s=mr.duration_s, dataset_name="SpaceNet", dataset_split="test",
                label_space="spacenet_14", has_ground_truth=bool(mr.ground_truth),
            ))
            for index, gt in enumerate(mr.ground_truth):
                session.add(GroundTruthModel(
                    id=f"gt_{mr.name}_{index}", recording_id=f"rec_{mr.name}",
                    t_start_s=gt.t_start_s, t_end_s=gt.t_end_s, f_low_hz=gt.f_low_hz,
                    f_high_hz=gt.f_high_hz, class_id=gt.class_id, class_name=gt.class_name,
                ))
        session.commit()


def _post(client, zip_path: Path):
    data = zip_path.read_bytes()
    return client.post(
        "/api/imported-runs/batch",
        files={"file": ("batch.zip", data, "application/zip")},
    )


# ---------------------------------------------------------------------------
# A1/A2
# ---------------------------------------------------------------------------
def test_a1_a2_exporter_package_and_external_archive_hash(tmp_path: Path) -> None:
    from hashlib import sha256

    result, _ = _build_batch(tmp_path)
    assert result.output_path.is_file()
    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.namelist() == [
            "batch_manifest.json",
            "items/000000/manifest.json",
            "items/000000/detections.json",
            "items/000001/manifest.json",
            "items/000001/detections.json",
        ]
        raw = archive.read("batch_manifest.json").decode("utf-8")
    manifest = json.loads(raw)
    assert manifest["schema_version"] == 1
    assert "archive_sha256" not in manifest
    assert result.archive_sha256 == sha256(result.output_path.read_bytes()).hexdigest()
    BatchManifest.model_validate(manifest)


# ---------------------------------------------------------------------------
# A3/A4
# ---------------------------------------------------------------------------
def test_a3_a4_http_import_creates_runs_and_resolves_identities(client, tmp_path: Path) -> None:
    result, samples = _build_batch(tmp_path)
    _seed_recordings(client, samples)

    response = _post(client, result.output_path)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["created_runs"] == 2
    assert summary["existing_runs"] == 0
    assert summary["already_imported"] is False
    assert summary["matched_recordings"] == 2
    assert summary["missing_recordings"] == 0
    assert summary["ambiguous_recordings"] == 0
    assert summary["fingerprint_mismatches"] == 0
    assert summary["detection_count"] == 2
    assert summary["import_fingerprint"] == result.import_fingerprint

    mapping = summary["recording_run_mapping"]
    assert len(mapping) == 2
    for entry in mapping:
        run = client.get(f"/api/analysis-runs/{entry['analysis_run_id']}")
        assert run.status_code == 200
        assert run.json()["executor"] == "imported"
        assert run.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# A5
# ---------------------------------------------------------------------------
def test_a5_atomic_commit_and_detections(client, tmp_path: Path) -> None:
    result, samples = _build_batch(tmp_path)
    _seed_recordings(client, samples)
    summary = _post(client, result.output_path).json()

    total_detections = 0
    for entry in summary["recording_run_mapping"]:
        detections = client.get(f"/api/analysis-runs/{entry['analysis_run_id']}/detections")
        assert detections.status_code == 200
        total_detections += len(detections.json())
    assert total_detections == summary["detection_count"] == 2


def test_a5_partial_failure_rolls_back(client, tmp_path: Path, monkeypatch) -> None:
    import app.imported_runs.batch_service as batch_service
    from app.imported_runs.batch_service import BatchPackageImportService
    from app.analysis.model import AnalysisRunModel
    from app.labels.service import LabelSpaceService

    result, samples = _build_batch(tmp_path)
    _seed_recordings(client, samples)

    real_builder = batch_service.build_imported_run_models
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("injected failure on second item")
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(batch_service, "build_imported_run_models", flaky)
    with client.app.state.database.session_factory() as session:
        service = BatchPackageImportService(
            session, client.app.state.storage,
            LabelSpaceService(client.app.state.settings.label_space_root),
        )
        with pytest.raises(RuntimeError):
            service.import_batch(BytesIO(result.output_path.read_bytes()))
        session.rollback()
        committed = session.query(AnalysisRunModel).filter(
            AnalysisRunModel.executor == "imported"
        ).count()
    assert committed == 0


# ---------------------------------------------------------------------------
# A6
# ---------------------------------------------------------------------------
def test_a6_idempotent_reimport(client, tmp_path: Path) -> None:
    result, samples = _build_batch(tmp_path)
    _seed_recordings(client, samples)
    first = _post(client, result.output_path).json()
    assert first["created_runs"] == 2

    second = _post(client, result.output_path)
    assert second.status_code == 201
    summary = second.json()
    assert summary["already_imported"] is True
    assert summary["created_runs"] == 0
    assert summary["existing_runs"] == 2
    assert summary["created_detections"] == 0
    assert summary["import_fingerprint"] == first["import_fingerprint"]

    listing = client.get("/api/analysis-runs", params={"status": "completed"}).json()
    imported = [run for run in listing if run["executor"] == "imported"]
    assert len(imported) == 2


# ---------------------------------------------------------------------------
# A7
# ---------------------------------------------------------------------------
def test_a7_inconsistent_prior_state_fails_closed(client, tmp_path: Path) -> None:
    from app.analysis.model import AnalysisRunModel

    result, samples = _build_batch(tmp_path)
    _seed_recordings(client, samples)

    # Seed a partial prior semantic import: only item 000000 for this fingerprint.
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_partial", recording_id="rec_0", pipeline_id="pipeline_x",
            pipeline_version="1.0", executor="imported", status="completed",
            parameters_json={"batch_import": {
                "schema_version": 1, "batch_id": "batch_e2e", "item_key": "000000",
                "package_path": "items/000000", "import_fingerprint": result.import_fingerprint,
                "recording_fingerprint": "b" * 64, "archive_sha256": result.archive_sha256,
                "result_provenance": {}, "transport_provenance": {},
            }},
        ))
        session.commit()

    response = _post(client, result.output_path)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "BATCH_IMPORT_STATE_INCONSISTENT"


# ---------------------------------------------------------------------------
# A8
# ---------------------------------------------------------------------------
def test_a8_imported_batch_is_evaluation_ready(client, tmp_path: Path) -> None:
    result, samples = _build_batch(tmp_path)
    _seed_recordings(client, samples)
    _post(client, result.output_path)

    catalog = client.get("/api/dataset-benchmarks/imported-batches")
    assert catalog.status_code == 200
    entry = next(item for item in catalog.json() if item["import_fingerprint"] == result.import_fingerprint)
    assert entry["run_count"] == 2
    assert entry["detection_count"] == 2
    assert entry["ready"] is True

    resolved = client.post(
        "/api/dataset-benchmarks/resolve-imported-batch",
        json={"import_fingerprint": result.import_fingerprint},
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["resolved_recordings"] == 2
    assert body["missing_recordings"] == 0
    assert body["conflict_count"] == 0


# ---------------------------------------------------------------------------
# A9
# ---------------------------------------------------------------------------
def test_a9_no_remote_gpu_no_db_sync(client, tmp_path: Path, monkeypatch) -> None:
    import app.remote_execution.runtime as runtime_module

    def _forbidden(*args, **kwargs):
        raise AssertionError("remote_gpu must not be constructed on the import path")

    monkeypatch.setattr(runtime_module.RemoteGpuExecutorProvider, "__init__", _forbidden)

    result, samples = _build_batch(tmp_path)
    _seed_recordings(client, samples)
    response = _post(client, result.output_path)
    assert response.status_code == 201

    import os
    assert not [key for key in os.environ if key.startswith("WSP_REMOTE_")]
    assert importlib.util.find_spec("torch") is None
    assert importlib.util.find_spec("ultralytics") is None

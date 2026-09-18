"""Focused Analysis Bundle tests (P4).

Covers: path-free export, portable cross-path matching, no-rerun import as
ordinary completed imported runs, detection preservation, idempotence, clear
mismatch rejection, pipeline-absence viewing, and legacy batch import intact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import zipfile

import pytest

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.core.config import Settings
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.datasets.model import DatasetModel
from app.datasets.repository import get_or_create_dataset, refresh_dataset_stats
from app.detections.model import DetectionResultModel
from app.ground_truth.model import GroundTruthModel
from app.imported_runs.bundle_schema import ANALYSIS_BUNDLE_MANIFEST_FILENAME
from app.imported_runs.bundle_service import (
    AnalysisBundleExportService,
    AnalysisBundleImportService,
)
from app.recordings.model import RecordingModel

PIPELINE_ID = "ghost_pipeline"  # deliberately not installed anywhere
PIPELINE_VERSION = "9.9"
DATASET_NAME = "SpaceNet"
DATASET_SPLIT = "test"
LABEL_SPACE = "spacenet_14"

SAMPLE_RATE_HZ = 1_000_000.0
CENTER_FREQUENCY_HZ = 2_441_000_000.0
FREQUENCY_LOW_HZ = 2_440_500_000.0
FREQUENCY_HIGH_HZ = 2_441_500_000.0
NUM_SAMPLES = 100_000
DURATION_S = 0.1


def _new_app(tmp_path: Path, name: str):
    from app.main import create_app

    root = tmp_path / name
    settings = Settings(
        project_root=root,
        data_root=root / "data",
        label_space_root=Path(__file__).resolve().parents[2] / "label_spaces",
        database_url=f"sqlite:///{root / 'app.db'}",
    )
    return create_app(settings)


def _seed_completed_analysis(
    app, *, machine, local_root, path_for, status="completed", sample_rate_hz=SAMPLE_RATE_HZ
):
    """Seed a first-class Dataset + completed DatasetExperiment directly via ORM."""
    session = app.state.database.session_factory()
    dataset = get_or_create_dataset(
        session,
        adapter_id="spacenet",
        split=DATASET_SPLIT,
        local_root=local_root,
        name=DATASET_NAME,
        label_space=LABEL_SPACE,
    )
    recordings = []
    for index in range(2):
        recording_id = f"{machine}_rec_{index}"
        path = path_for(index)
        recording = RecordingModel(
            id=recording_id,
            name=f"{machine}_name_{index}",
            data_path=path,
            data_format="complex64_le",
            source="spacenet",
            external_path=path,
            sample_rate_hz=sample_rate_hz,
            center_frequency_hz=CENTER_FREQUENCY_HZ,
            frequency_low_hz=FREQUENCY_LOW_HZ,
            frequency_high_hz=FREQUENCY_HIGH_HZ,
            num_samples=NUM_SAMPLES,
            duration_s=DURATION_S,
            dataset_name=DATASET_NAME,
            dataset_split=DATASET_SPLIT,
            label_space=LABEL_SPACE,
            has_ground_truth=True,
            dataset_id=dataset.id,
            sample_key=f"{index:04d}",
        )
        session.add(recording)
        session.add(GroundTruthModel(
            id=f"gt_{recording_id}", recording_id=recording_id,
            t_start_s=0.01, t_end_s=0.02, f_low_hz=FREQUENCY_LOW_HZ,
            f_high_hz=FREQUENCY_LOW_HZ + 100_000.0, class_id=9, class_name="LoRa 250kHz",
        ))
        recordings.append(recording)
    session.flush()
    refresh_dataset_stats(session, dataset, recordings)

    experiment = DatasetExperimentModel(
        id=f"exp_{machine}", name="analysis",
        dataset_name=DATASET_NAME, dataset_split=DATASET_SPLIT,
        dataset_label_space=LABEL_SPACE, dataset_id=dataset.id,
        recording_manifest_hash="a" * 64,
        plugin_id=PIPELINE_ID, plugin_version=PIPELINE_VERSION,
        parameters_json={"threshold": 0.5},
        executor="local_cpu", runtime_descriptor_json={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
        status=status, completed_at=datetime.now(timezone.utc),
    )
    session.add(experiment)
    for index, recording in enumerate(recordings):
        item_id = f"item_{machine}_{index}"
        run_id = f"{machine}_run_{index}"
        session.add(DatasetExperimentItemModel(
            id=item_id, experiment_id=experiment.id, manifest_order=index,
            recording_id=recording.id, status="completed",
        ))
        session.add(DatasetExperimentAttemptModel(
            id=f"att_{machine}_{index}", experiment_item_id=item_id,
            attempt_number=1, analysis_run_id=run_id,
        ))
        session.add(AnalysisRunModel(
            id=run_id, recording_id=recording.id, pipeline_id=PIPELINE_ID,
            pipeline_version=PIPELINE_VERSION, executor="local_cpu", status="completed",
            parameters_json={},
        ))
        session.add(DetectionResultModel(
            id=f"det_{machine}_{index}", run_id=run_id,
            t_start_s=0.011, t_end_s=0.022, f_low_hz=FREQUENCY_LOW_HZ,
            f_high_hz=FREQUENCY_LOW_HZ + 100_000.0, class_id=9,
            class_name="LoRa 250kHz", confidence=0.9,
        ))
    session.commit()
    return session, experiment.id, dataset.id, [recording.id for recording in recordings]


def _seed_source_evaluation(session, experiment_id, dataset, run_ids):
    """Link a completed evaluation snapshot to the source experiment (P4.1 export)."""
    evaluation = DatasetEvaluationModel(
        id=f"eval_{experiment_id}", name="source evaluation",
        dataset_name=DATASET_NAME, dataset_split=DATASET_SPLIT, label_space=LABEL_SPACE,
        dataset_id=dataset.id, pipeline_id=PIPELINE_ID, pipeline_version=PIPELINE_VERSION,
        status="completed", expected_recordings=2, evaluated_recordings=2,
        missing_recordings=0, coverage=1.0, comparable=True,
        recording_manifest_hash="b" * 64,
        evaluation_protocol="physical_tf_detection_ap_v2", protocol_config_json={},
        aggregate_metrics_json={"localization": {"ap50": 0.87}},
        per_class_metrics_json=[{"class_id": 9, "class_name": "LoRa 250kHz", "ap50": 0.87}],
        confusion_json=[{"gt_class_id": 9, "pred_class_id": 9, "count": 2}],
        completed_at=datetime.now(timezone.utc),
    )
    session.add(evaluation)
    for index, run_id in enumerate(run_ids):
        run = session.get(AnalysisRunModel, run_id)
        session.add(DatasetEvaluationItemModel(
            id=f"evalitem_{experiment_id}_{index}", evaluation_id=evaluation.id,
            manifest_order=index, recording_id=run.recording_id,
            analysis_run_id=run_id, status="completed", gt_count=1, prediction_count=1,
        ))
    experiment = session.get(DatasetExperimentModel, experiment_id)
    experiment.dataset_evaluation_id = evaluation.id
    session.commit()
    return evaluation


def _imported_experiments(session):
    return session.query(DatasetExperimentModel).filter_by(executor="imported").all()


def _craft_bundle(payload: bytes, tmp_path: Path, *, name: str, keep_keys: set, status: str) -> BytesIO:
    """Rewrite an exported bundle to a partial subset/status for fail-closed tests."""
    import json

    extract_dir = tmp_path / name
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        archive.extractall(extract_dir)
    manifest_path = extract_dir / ANALYSIS_BUNDLE_MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["samples"] = [sample for sample in manifest["samples"] if sample["key"] in keep_keys]
    manifest["analysis"]["status"] = status
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return _zip_bytes(extract_dir)


def _zip_bytes(root: Path) -> BytesIO:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    buffer.seek(0)
    return buffer


def _export(app, experiment_id: str) -> bytes:
    session = app.state.database.session_factory()
    filename, payload = AnalysisBundleExportService(session).export_experiment(experiment_id)
    assert filename.endswith(".zip")
    return payload


def _windows_paths(index: int) -> str:
    return rf"D:\SpaceNet\test\{index:04d}.bin"


# ---------- export: portable, path-free ----------

def test_export_bundle_contains_no_raw_iq_and_no_local_paths(tmp_path):
    app = _new_app(tmp_path, "machine_a")
    _, experiment_id, _, _ = _seed_completed_analysis(
        app, machine="win", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        assert ANALYSIS_BUNDLE_MANIFEST_FILENAME in names
        assert all(name.endswith(".json") for name in names)
        assert all(not name.endswith((".bin", ".iq", ".npy", ".dat")) for name in names)
        blob = b"".join(archive.read(name) for name in names)
    for forbidden in (
        b"local_root", b"external_path", b"data_path",
        b"D:\\", b"C:\\", b"/data/", b".bin", b"raw.iq",
    ):
        assert forbidden not in blob, forbidden


def test_export_rejects_incomplete_analysis(tmp_path):
    app = _new_app(tmp_path, "machine_incomplete")
    _, experiment_id, _, _ = _seed_completed_analysis(
        app, machine="pending", local_root=r"D:\SpaceNet",
        path_for=_windows_paths, status="pending",
    )
    session = app.state.database.session_factory()
    with pytest.raises(PlatformError) as excinfo:
        AnalysisBundleExportService(session).export_experiment(experiment_id)
    assert excinfo.value.code == "ANALYSIS_BUNDLE_NOT_COMPLETED"


# ---------- portable cross-machine matching ----------

def test_windows_and_linux_datasets_match_by_portable_identity(tmp_path):
    machine_a = _new_app(tmp_path, "win_machine")
    _, experiment_id, _, _ = _seed_completed_analysis(
        machine_a, machine="win", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(machine_a, experiment_id)

    machine_b = _new_app(tmp_path, "linux_machine")
    session_b, _, dataset_b_id, recordings_b = _seed_completed_analysis(
        machine_b, machine="nix", local_root="/data/SpaceNet",
        path_for=lambda i: f"/data/SpaceNet/test/{i:04d}.bin",
    )
    summary = AnalysisBundleImportService(
        session_b, machine_b.state.storage
    ).import_bundle(BytesIO(payload))

    assert summary.created_runs == 2
    assert summary.dataset_id == session_b.get(DatasetModel, dataset_b_id).id
    assert {row.recording_id for row in summary.sample_run_mapping} == set(recordings_b)


# ---------- import creates ordinary completed imported runs + detections ----------

def test_import_creates_completed_imported_runs_and_preserves_detections(tmp_path):
    app = _new_app(tmp_path, "roundtrip")
    session, experiment_id, _, recording_ids = _seed_completed_analysis(
        app, machine="rt", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    summary = AnalysisBundleImportService(session, app.state.storage).import_bundle(BytesIO(payload))

    assert summary.already_imported is False
    assert summary.sample_count == 2
    assert summary.detection_count == 2
    assert summary.created_runs == 2
    assert summary.created_detections == 2

    imported = session.query(AnalysisRunModel).filter_by(executor="imported").all()
    assert len(imported) == 2
    for run in imported:
        assert run.status == "completed"
        assert run.recording_id in recording_ids
        assert run.pipeline_id == PIPELINE_ID
        assert run.parameters_json["analysis_bundle"]["import_fingerprint"]
        detections = session.query(DetectionResultModel).filter_by(run_id=run.id).all()
        assert len(detections) == 1
        assert detections[0].class_name == "LoRa 250kHz"
        assert detections[0].confidence == pytest.approx(0.9)


def test_duplicate_import_is_idempotent(tmp_path):
    app = _new_app(tmp_path, "idem")
    session, experiment_id, _, _ = _seed_completed_analysis(
        app, machine="idem", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    service = AnalysisBundleImportService(session, app.state.storage)

    first = service.import_bundle(BytesIO(payload))
    second = service.import_bundle(BytesIO(payload))

    assert second.already_imported is True
    assert second.created_runs == 0
    assert second.created_detections == 0
    assert second.existing_runs == 2
    assert second.sample_run_mapping == first.sample_run_mapping
    imported_runs = session.query(AnalysisRunModel).filter_by(executor="imported").all()
    assert len(imported_runs) == 2
    imported_run_ids = [run.id for run in imported_runs]
    assert session.query(DetectionResultModel).filter(
        DetectionResultModel.run_id.in_(imported_run_ids)
    ).count() == 2


def test_mismatched_dataset_rejected_clearly(tmp_path):
    source = _new_app(tmp_path, "source")
    _, experiment_id, _, _ = _seed_completed_analysis(
        source, machine="src", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(source, experiment_id)

    other = _new_app(tmp_path, "other")
    other_session, _, _, _ = _seed_completed_analysis(
        other, machine="other", local_root=r"D:\DifferentDataset",
        path_for=lambda i: rf"D:\DifferentDataset\{i:04d}.bin",
        sample_rate_hz=2_000_000.0,
    )
    with pytest.raises(PlatformError) as excinfo:
        AnalysisBundleImportService(other_session, other.state.storage).import_bundle(BytesIO(payload))
    assert excinfo.value.code == "ANALYSIS_BUNDLE_DATASET_MISMATCH"


# ---------- pipeline absence does not block import or viewing ----------

def test_pipeline_absence_does_not_prevent_import_or_viewing(client):
    app = client.app
    session, experiment_id, _, _ = _seed_completed_analysis(
        app, machine="ghost", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    summary = AnalysisBundleImportService(session, app.state.storage).import_bundle(BytesIO(payload))
    assert summary.created_runs == 2

    run_id = summary.sample_run_mapping[0].analysis_run_id
    # The ghost pipeline is not registered; reading the run and its detections still works.
    assert client.get(f"/api/analysis-runs/{run_id}").status_code == 200
    response = client.get(f"/api/analysis-runs/{run_id}/detections")
    assert response.status_code == 200
    assert len(response.json()) == 1


# ---------- REST endpoints ----------

def test_export_and_import_endpoints(client):
    _, experiment_id, _, _ = _seed_completed_analysis(
        client.app, machine="api", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    export = client.get(f"/api/dataset-experiments/{experiment_id}/export")
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/zip"
    assert export.content[:2] == b"PK"

    imported = client.post(
        "/api/analysis-bundles/import",
        files={"file": ("bundle.zip", export.content, "application/zip")},
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["created_runs"] == 2
    assert body["pipeline_id"] == PIPELINE_ID


# ---------- legacy batch import intact ----------

def test_legacy_batch_import_endpoint_still_works(client, tmp_path):
    import batch_import_fixture as fix

    session = client.app.state.database.session_factory()
    root, _ = fix.build_complete_batch(session, tmp_path)
    response = client.post(
        "/api/imported-runs/batch",
        files={"file": ("batch.zip", _zip_bytes(root).getvalue(), "application/zip")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["created_runs"] == 2


# ---------- P4.1: durable imported Dataset Analysis ----------

def test_import_creates_durable_imported_dataset_analysis(tmp_path):
    """A/B/D/E/F: the imported bundle becomes a first-class Dataset Analysis."""
    app = _new_app(tmp_path, "durable")
    session, experiment_id, dataset_id, _ = _seed_completed_analysis(
        app, machine="dur", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    summary = AnalysisBundleImportService(session, app.state.storage).import_bundle(BytesIO(payload))

    assert summary.dataset_analysis_id is not None
    experiment = session.get(DatasetExperimentModel, summary.dataset_analysis_id)
    assert experiment is not None
    assert experiment.executor == "imported"
    assert experiment.status == "completed"
    assert experiment.dataset_id == dataset_id  # B: matched local Dataset authority
    assert experiment.dataset_name == DATASET_NAME
    assert experiment.plugin_id == PIPELINE_ID
    assert experiment.evaluation_protocol == "physical_tf_detection_ap_v2"
    provenance = experiment.runtime_descriptor_json["analysis_bundle"]
    assert provenance["bundle_id"] == summary.bundle_id
    assert provenance["import_fingerprint"] == summary.import_fingerprint
    assert experiment.recording_manifest_hash  # current local manifest hash

    items = session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment.id
    ).order_by(DatasetExperimentItemModel.manifest_order).all()
    assert len(items) == 2
    assert all(item.status == "completed" for item in items)  # D
    imported_runs = {run.id for run in session.query(AnalysisRunModel).filter_by(executor="imported")}
    for item in items:
        attempts = session.query(DatasetExperimentAttemptModel).filter_by(
            experiment_item_id=item.id
        ).all()
        assert len(attempts) == 1
        assert attempts[0].attempt_number == 1
        assert attempts[0].analysis_run_id in imported_runs  # E

    # F: latest_analysis_run_id works through the existing read model.
    service = DatasetExperimentService(session, None, None, None)
    listed = service.list_items(experiment.id)
    assert {row.latest_analysis_run_id for row in listed} == imported_runs


def test_imported_experiment_appears_in_dataset_listing(tmp_path):
    """C: Dataset → Analyses discovers the imported analysis via dataset_id."""
    app = _new_app(tmp_path, "discoverable")
    session, experiment_id, dataset_id, _ = _seed_completed_analysis(
        app, machine="disc", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    summary = AnalysisBundleImportService(session, app.state.storage).import_bundle(BytesIO(payload))

    listed = DatasetExperimentService(session, None, None, None).list_experiments(dataset_id=dataset_id)
    assert summary.dataset_analysis_id in {row.id for row in listed}


def test_same_bundle_twice_creates_one_dataset_analysis(tmp_path):
    """G: experiment-level idempotence."""
    app = _new_app(tmp_path, "expidem")
    session, experiment_id, _, _ = _seed_completed_analysis(
        app, machine="expidem", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    service = AnalysisBundleImportService(session, app.state.storage)
    first = service.import_bundle(BytesIO(payload))
    second = service.import_bundle(BytesIO(payload))

    assert first.dataset_analysis_id == second.dataset_analysis_id
    experiments = _imported_experiments(session)
    assert len(experiments) == 1
    assert session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiments[0].id
    ).count() == 2
    assert session.query(DatasetExperimentAttemptModel).join(
        DatasetExperimentItemModel,
        DatasetExperimentAttemptModel.experiment_item_id == DatasetExperimentItemModel.id,
    ).filter(DatasetExperimentItemModel.experiment_id == experiments[0].id).count() == 2


def test_re_exported_bundle_is_still_deduplicated(tmp_path):
    """Idempotence must survive re-export.

    The portable identity may not depend on the LOCAL detection primary key: an
    exported bundle carries ``det_...`` ids, and importing regenerates them, so
    re-exporting an already-imported analysis must still be recognized as the
    same results (no duplicate runs / Dataset Analysis).
    """
    source = _new_app(tmp_path, "reexport_src")
    _, experiment_id, _, _ = _seed_completed_analysis(
        source, machine="rex", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    original = _export(source, experiment_id)

    target = _new_app(tmp_path, "reexport_tgt")
    target_session, _, _, _ = _seed_completed_analysis(
        target, machine="retgt", local_root="/data/SpaceNet",
        path_for=lambda i: f"/data/SpaceNet/test/{i:04d}.bin",
    )
    service = AnalysisBundleImportService(target_session, target.state.storage)
    first = service.import_bundle(BytesIO(original))

    # Re-export the IMPORTED analysis (its detections now carry fresh local ids)
    # and import that file back: it is the same portable results.
    re_exported = _export(target, first.dataset_analysis_id)
    second = service.import_bundle(BytesIO(re_exported))

    assert second.already_imported is True
    assert second.created_runs == 0
    assert second.dataset_analysis_id == first.dataset_analysis_id
    assert len(_imported_experiments(target_session)) == 1
    assert target_session.query(AnalysisRunModel).filter_by(executor="imported").count() == 2


def test_backfills_shell_around_existing_imported_runs(tmp_path):
    """H: runs exist (pre-shell state) -> importing backfills the shell around them."""
    app = _new_app(tmp_path, "backfill")
    session, experiment_id, _, _ = _seed_completed_analysis(
        app, machine="bf", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    service = AnalysisBundleImportService(session, app.state.storage)
    first = service.import_bundle(BytesIO(payload))
    first_run_ids = {row.analysis_run_id for row in first.sample_run_mapping}

    # Simulate the pre-P4.1 state: runs exist, the shell does not.
    shell = session.get(DatasetExperimentModel, first.dataset_analysis_id)
    session.delete(shell)
    session.commit()
    assert _imported_experiments(session) == []
    assert session.query(AnalysisRunModel).filter_by(executor="imported").count() == 2

    second = service.import_bundle(BytesIO(payload))
    assert second.already_imported is True
    assert second.created_runs == 0
    assert second.dataset_analysis_id is not None
    assert second.dataset_analysis_id != first.dataset_analysis_id
    experiment = session.get(DatasetExperimentModel, second.dataset_analysis_id)
    assert experiment is not None
    assert len(_imported_experiments(session)) == 1
    items = session.query(DatasetExperimentItemModel).filter_by(experiment_id=experiment.id).all()
    assert len(items) == 2 and all(item.status == "completed" for item in items)
    attempt_runs = {
        attempt.analysis_run_id
        for attempt in session.query(DatasetExperimentAttemptModel).join(
            DatasetExperimentItemModel,
            DatasetExperimentAttemptModel.experiment_item_id == DatasetExperimentItemModel.id,
        ).filter(DatasetExperimentItemModel.experiment_id == experiment.id).all()
    }
    assert attempt_runs == first_run_ids  # attempts point at the existing runs


def test_completed_bundle_must_cover_full_dataset(tmp_path):
    """I: a completed bundle that misses Dataset members fails closed."""
    source = _new_app(tmp_path, "partial_source")
    _, experiment_id, _, _ = _seed_completed_analysis(
        source, machine="psrc", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(source, experiment_id)
    crafted = _craft_bundle(
        payload, tmp_path, name="partial", keep_keys={"0000"}, status="completed",
    )

    target = _new_app(tmp_path, "partial_target")
    target_session, _, _, _ = _seed_completed_analysis(
        target, machine="ptgt", local_root="/data/SpaceNet",
        path_for=lambda i: f"/data/SpaceNet/test/{i:04d}.bin",
    )
    with pytest.raises(PlatformError) as excinfo:
        AnalysisBundleImportService(target_session, target.state.storage).import_bundle(crafted)
    assert excinfo.value.code == "ANALYSIS_BUNDLE_INCOMPLETE_FOR_COMPLETED"
    # Fail closed: no runs and no imported Dataset Analysis were created.
    assert target_session.query(AnalysisRunModel).filter_by(executor="imported").count() == 0
    assert _imported_experiments(target_session) == []


def test_completed_with_failures_creates_failed_items_honestly(tmp_path):
    """J: partial bundle -> failed items with a marker, no fake runs."""
    source = _new_app(tmp_path, "cwf_source")
    _, experiment_id, _, _ = _seed_completed_analysis(
        source, machine="csrc", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(source, experiment_id)
    crafted = _craft_bundle(
        payload, tmp_path, name="cwf", keep_keys={"0000"}, status="completed_with_failures",
    )

    target = _new_app(tmp_path, "cwf_target")
    target_session, _, _, _ = _seed_completed_analysis(
        target, machine="ctgt", local_root="/data/SpaceNet",
        path_for=lambda i: f"/data/SpaceNet/test/{i:04d}.bin",
    )
    summary = AnalysisBundleImportService(
        target_session, target.state.storage
    ).import_bundle(crafted)

    experiment = target_session.get(DatasetExperimentModel, summary.dataset_analysis_id)
    assert experiment.status == "completed_with_failures"
    items = target_session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment.id
    ).order_by(DatasetExperimentItemModel.manifest_order).all()
    assert len(items) == 2
    completed = [item for item in items if item.status == "completed"]
    failed = [item for item in items if item.status == "failed"]
    assert len(completed) == 1 and len(failed) == 1
    assert failed[0].last_error_type == "ANALYSIS_BUNDLE_RESULT_UNAVAILABLE"
    # No fake AnalysisRun for the failed member.
    assert target_session.query(AnalysisRunModel).filter_by(
        recording_id=failed[0].recording_id, executor="imported"
    ).count() == 0
    attempts = target_session.query(DatasetExperimentAttemptModel).filter_by(
        experiment_item_id=completed[0].id
    ).count()
    assert attempts == 1
    assert summary.created_runs == 1


def test_completed_evaluation_snapshot_is_restored(tmp_path):
    """K/L/M: linked completed evaluation is restored through existing tables."""
    source = _new_app(tmp_path, "eval_source")
    source_session, experiment_id, source_dataset_id, _ = _seed_completed_analysis(
        source, machine="esrc", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    source_runs = [f"esrc_run_{index}" for index in range(2)]
    source_dataset = source_session.get(DatasetModel, source_dataset_id)
    _seed_source_evaluation(source_session, experiment_id, source_dataset, source_runs)
    payload = _export(source, experiment_id)

    with zipfile.ZipFile(BytesIO(payload)) as archive:
        manifest = archive.read(ANALYSIS_BUNDLE_MANIFEST_FILENAME).decode("utf-8")
    assert '"confusion"' in manifest  # exporter carries the confusion matrix

    target = _new_app(tmp_path, "eval_target")
    target_session, _, dataset_id, _ = _seed_completed_analysis(
        target, machine="etgt", local_root="/data/SpaceNet",
        path_for=lambda i: f"/data/SpaceNet/test/{i:04d}.bin",
    )
    summary = AnalysisBundleImportService(target_session, target.state.storage).import_bundle(BytesIO(payload))

    experiment = target_session.get(DatasetExperimentModel, summary.dataset_analysis_id)
    assert experiment.dataset_evaluation_id is not None
    evaluation = target_session.get(DatasetEvaluationModel, experiment.dataset_evaluation_id)
    assert evaluation.status == "completed"
    assert evaluation.dataset_id == dataset_id  # L: local dataset authority
    assert evaluation.evaluation_protocol == "physical_tf_detection_ap_v2"
    assert evaluation.aggregate_metrics_json == {"localization": {"ap50": 0.87}}
    assert evaluation.per_class_metrics_json == [
        {"class_id": 9, "class_name": "LoRa 250kHz", "ap50": 0.87}
    ]
    assert evaluation.confusion_json == [{"gt_class_id": 9, "pred_class_id": 9, "count": 2}]
    imported_runs = {run.id for run in target_session.query(AnalysisRunModel).filter_by(executor="imported")}
    eval_items = target_session.query(DatasetEvaluationItemModel).filter_by(
        evaluation_id=evaluation.id
    ).order_by(DatasetEvaluationItemModel.manifest_order).all()
    assert len(eval_items) == 2
    assert {item.analysis_run_id for item in eval_items} == imported_runs  # M
    assert all(item.status == "completed" for item in eval_items)


def test_missing_evaluation_summary_leaves_evaluation_null(tmp_path):
    """N: no evaluation snapshot remains a valid imported analysis."""
    app = _new_app(tmp_path, "noeval")
    session, experiment_id, _, _ = _seed_completed_analysis(
        app, machine="noeval", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    payload = _export(app, experiment_id)
    summary = AnalysisBundleImportService(session, app.state.storage).import_bundle(BytesIO(payload))

    experiment = session.get(DatasetExperimentModel, summary.dataset_analysis_id)
    assert experiment is not None
    assert experiment.dataset_evaluation_id is None


@pytest.mark.parametrize(
    "evaluation_summary",
    [
        {"status": "pending"},
        {"status": "completed"},
        {"status": "completed", "aggregate_metrics": "not-a-dict"},
        {"status": "completed", "aggregate_metrics": ["not", "a", "dict"]},
    ],
)
def test_malformed_evaluation_snapshot_does_not_block_import(tmp_path, evaluation_summary):
    """P: a structurally incomplete optional evaluation snapshot never blocks import.

    The transported evaluation snapshot is deliberately loose, transported data.
    Any reconstruction problem must degrade to "analysis imported, evaluation
    unlinked" rather than failing the whole Analysis result import.
    """
    import json

    source = _new_app(tmp_path, "bad_eval_source")
    source_session, experiment_id, source_dataset_id, _ = _seed_completed_analysis(
        source, machine="bsrc", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    source_dataset = source_session.get(DatasetModel, source_dataset_id)
    _seed_source_evaluation(
        source_session, experiment_id, source_dataset,
        [f"bsrc_run_{index}" for index in range(2)],
    )
    payload = _export(source, experiment_id)

    extract_dir = tmp_path / "bad_eval_bundle"
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        archive.extractall(extract_dir)
    manifest_path = extract_dir / ANALYSIS_BUNDLE_MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance"]["evaluation_summary"] = evaluation_summary
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    target = _new_app(tmp_path, "bad_eval_target")
    target_session, _, _, _ = _seed_completed_analysis(
        target, machine="btgt", local_root="/data/SpaceNet",
        path_for=lambda i: f"/data/SpaceNet/test/{i:04d}.bin",
    )
    summary = AnalysisBundleImportService(
        target_session, target.state.storage
    ).import_bundle(_zip_bytes(extract_dir))

    experiment = target_session.get(DatasetExperimentModel, summary.dataset_analysis_id)
    assert experiment is not None
    assert experiment.status == "completed"
    assert experiment.dataset_evaluation_id is None
    assert target_session.query(AnalysisRunModel).filter_by(executor="imported").count() == 2


def test_pipeline_absence_does_not_block_experiment_import(tmp_path):
    """O: the imported Dataset Analysis exists without the pipeline installed."""
    app = _new_app(tmp_path, "ghostshell")
    session, experiment_id, _, _ = _seed_completed_analysis(
        app, machine="gshell", local_root=r"D:\SpaceNet", path_for=_windows_paths,
    )
    # The ghost pipeline is not registered anywhere.
    from app.core.errors import PlatformError as _PE
    with pytest.raises(_PE):
        app.state.pipeline_registry.get(PIPELINE_ID)

    payload = _export(app, experiment_id)
    summary = AnalysisBundleImportService(session, app.state.storage).import_bundle(BytesIO(payload))
    experiment = session.get(DatasetExperimentModel, summary.dataset_analysis_id)
    assert experiment is not None
    assert experiment.plugin_id == PIPELINE_ID
    assert experiment.executor == "imported"

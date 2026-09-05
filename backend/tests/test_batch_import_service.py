from io import BytesIO
from pathlib import Path
import zipfile

import pytest

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.imported_runs.batch_service import BatchPackageImportService
from app.labels.service import LabelSpaceService

import batch_import_fixture as fix


@pytest.fixture
def service(client, settings):
    return BatchPackageImportService(
        client.app.state.database.session_factory().__enter__()
        if False else _session(client), client.app.state.storage,
        LabelSpaceService(settings.label_space_root),
    )


def _session(client):
    from app.db.base import Base, load_domain_models
    from app.db.session import Database
    from app.db.migrations import run_additive_migrations
    database = Database(client.app.state.settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    return database.session_factory().__enter__()


def _zip_root(root: Path) -> BytesIO:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    buffer.seek(0)
    return buffer


def _build_zip(client, tmp_path, **kwargs) -> BytesIO:
    session = _session(client)
    root, _ = fix.build_complete_batch(session, tmp_path, **kwargs)
    return _zip_root(root)


def test_happy_path_import_creates_standard_runs(client, tmp_path, settings):
    service = BatchPackageImportService(_session(client), client.app.state.storage,
                                        LabelSpaceService(settings.label_space_root))
    result = service.import_batch(_build_zip(client, tmp_path))
    assert result.already_imported is False
    assert result.item_count == 2
    assert result.created_runs == 2
    assert result.created_detections == 1
    assert len(result.recording_run_mapping) == 2
    session = _session(client)
    assert session.query(AnalysisRunModel).count() == 2
    assert session.query(DetectionResultModel).count() == 1
    run = session.query(AnalysisRunModel).first()
    assert run.executor == "imported"
    assert run.status == "completed"
    batch_import = run.parameters_json["batch_import"]
    assert batch_import["schema_version"] == 1
    assert batch_import["batch_id"]
    assert batch_import["item_key"]
    assert batch_import["import_fingerprint"]
    assert batch_import["recording_fingerprint"]
    assert batch_import["archive_sha256"]
    assert batch_import["result_provenance"] == {
        "code_commit": None,
        "config_sha256": "a" * 64,
        "split_manifest_sha256": None,
        "source_predictions_sha256": None,
        "artifact_sha256": {},
    }
    assert batch_import["transport_provenance"] == {
        "exporter_version": "batch_analysis_package_v1",
        "platform_repo_commit": None,
        "export_timestamp": None,
    }


def test_one_invalid_child_leaves_zero_rows(client, tmp_path, settings):
    session = _session(client)
    fix.seed_local_recordings(session)
    root = Path(tmp_path) / "batch"
    root.mkdir(parents=True)
    fingerprints = fix.local_fingerprints(session)
    fix.write_child_package(root, "000000", name="a", fingerprint_sha256=fingerprints["a"].sha256,
                            detections=[fix.detection()])
    fix.write_child_package(root, "000001", name="b", fingerprint_sha256=fingerprints["b"].sha256,
                            detections=[fix.detection(confidence=1.5)])  # invalid confidence
    outer = fix.build_outer_manifest(session, fingerprints=fingerprints)
    (root / "batch_manifest.json").write_text(outer if isinstance(outer, str) else __import__("json").dumps(outer), encoding="utf-8")
    service = BatchPackageImportService(_session(client), client.app.state.storage,
                                        LabelSpaceService(settings.label_space_root))
    with pytest.raises(PlatformError):
        service.import_batch(_zip_root(root))
    fresh = _session(client)
    assert fresh.query(AnalysisRunModel).count() == 0
    assert fresh.query(DetectionResultModel).count() == 0


def test_scale_atomicity_2500_with_one_invalid(client, tmp_path, settings):
    session = _session(client)
    names = [f"{i:04d}" for i in range(2500)]
    fix.seed_local_recordings(session, names=names)
    from app.recordings.model import RecordingModel
    from app.ground_truth.model import GroundTruthModel
    # add GT for all 2500 recordings
    for i, name in enumerate(names):
        rec = session.query(RecordingModel).filter(RecordingModel.name == name).one()
        session.add(GroundTruthModel(id=f"gt_scale_{i}", recording_id=rec.id, t_start_s=0.01, t_end_s=0.02,
                                     f_low_hz=fix.FREQUENCY_LOW_HZ, f_high_hz=fix.FREQUENCY_LOW_HZ + 100_000.0,
                                     class_id=9, class_name="LoRa 250kHz"))
    session.commit()
    fingerprints = fix.local_fingerprints(session)
    root = Path(tmp_path) / "batch"
    root.mkdir(parents=True)
    for index, name in enumerate(names):
        dets = [fix.detection()] if index < 2 else []
        if index == 2499:
            dets = [fix.detection(confidence=1.5)]
        fix.write_child_package(root, f"{index:06d}", name=name,
                                fingerprint_sha256=fingerprints[name].sha256, detections=dets)
    outer = fix.build_outer_manifest(session, fingerprints=fingerprints, item_order=names)
    (root / "batch_manifest.json").write_text(__import__("json").dumps(outer), encoding="utf-8")
    service = BatchPackageImportService(_session(client), client.app.state.storage,
                                        LabelSpaceService(settings.label_space_root))
    with pytest.raises(PlatformError):
        service.import_batch(_zip_root(root))
    fresh = _session(client)
    assert fresh.query(AnalysisRunModel).count() == 0
    assert fresh.query(DetectionResultModel).count() == 0


def test_db_commit_failure_rolls_back_all(client, tmp_path, settings, monkeypatch):
    service = BatchPackageImportService(_session(client), client.app.state.storage,
                                        LabelSpaceService(settings.label_space_root))
    original = _session(client).__class__
    import app.imported_runs.batch_service as bs

    def _boom(*args, **kwargs):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(bs.Session, "commit", _boom)
    with pytest.raises(RuntimeError):
        service.import_batch(_build_zip(client, tmp_path))
    fresh = _session(client)
    assert fresh.query(AnalysisRunModel).count() == 0
    assert fresh.query(DetectionResultModel).count() == 0


def test_full_idempotency_second_import_zero_rows(client, tmp_path, settings):
    service = BatchPackageImportService(_session(client), client.app.state.storage,
                                        LabelSpaceService(settings.label_space_root))
    zip_bytes = _build_zip(client, tmp_path)
    first = service.import_batch(zip_bytes)
    second = service.import_batch(zip_bytes)
    assert second.already_imported is True
    assert second.created_runs == 0
    assert second.created_detections == 0
    assert second.existing_runs == 2
    assert second.recording_run_mapping == first.recording_run_mapping
    session = _session(client)
    assert session.query(AnalysisRunModel).count() == 2
    assert session.query(DetectionResultModel).count() == 1


def test_partial_prior_state_raises_inconsistent(client, tmp_path, settings):
    service = BatchPackageImportService(_session(client), client.app.state.storage,
                                        LabelSpaceService(settings.label_space_root))
    zip_bytes = _build_zip(client, tmp_path)
    service.import_batch(zip_bytes)
    # Delete exactly one imported run, leaving partial semantic state.
    session = _session(client)
    run = session.query(AnalysisRunModel).first()
    session.query(DetectionResultModel).filter(DetectionResultModel.run_id == run.id).delete()
    session.delete(run)
    session.commit()
    with pytest.raises(PlatformError) as exc:
        service.import_batch(zip_bytes)
    assert exc.value.code == "BATCH_IMPORT_STATE_INCONSISTENT"
    # no new rows and no repair of the deleted run
    session = _session(client)
    assert session.query(AnalysisRunModel).count() == 1


def test_prior_semantic_state_with_wrong_recording_mapping_is_inconsistent(client, tmp_path, settings):
    service = BatchPackageImportService(_session(client), client.app.state.storage,
                                        LabelSpaceService(settings.label_space_root))
    zip_bytes = _build_zip(client, tmp_path)
    service.import_batch(zip_bytes)
    # Swap the batch_import.item_key between the two existing runs while keeping
    # the same import_fingerprint, creating a wrong item-key/Recording mapping.
    session = _session(client)
    runs = session.query(AnalysisRunModel).all()
    assert len(runs) == 2
    run_a, run_b = runs
    key_a = run_a.parameters_json["batch_import"]["item_key"]
    key_b = run_b.parameters_json["batch_import"]["item_key"]
    params_a = dict(run_a.parameters_json)
    params_a["batch_import"] = dict(params_a["batch_import"])
    params_a["batch_import"]["item_key"] = key_b
    run_a.parameters_json = params_a
    params_b = dict(run_b.parameters_json)
    params_b["batch_import"] = dict(params_b["batch_import"])
    params_b["batch_import"]["item_key"] = key_a
    run_b.parameters_json = params_b
    session.commit()
    with pytest.raises(PlatformError) as exc:
        service.import_batch(zip_bytes)
    assert exc.value.code == "BATCH_IMPORT_STATE_INCONSISTENT"
    # no automatic repair
    session = _session(client)
    assert session.query(AnalysisRunModel).count() == 2
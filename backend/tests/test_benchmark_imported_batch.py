import pytest

from benchmark_fixture import add_detection, add_ground_truth, add_recording, add_run
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError

FINGERPRINT = "a" * 64


def batch_parameters(item_key: str, *, fingerprint=FINGERPRINT, recording_manifest_hash=None):
    batch_import = {
            "schema_version": 1,
            "batch_id": "batch_x",
            "item_key": item_key,
            "package_path": f"items/{item_key}.analysis.zip",
            "import_fingerprint": fingerprint,
            "recording_fingerprint": "b" * 64,
            "archive_sha256": "c" * 64,
            "result_provenance": {"source_predictions_sha256": "d" * 64},
            "transport_provenance": {"exporter_version": "m8_6b_v1"},
        }
    if recording_manifest_hash is not None:
        batch_import["recording_manifest_hash"] = recording_manifest_hash
    return {"batch_import": batch_import}


def seed_recording(session, recording_id: str, name: str):
    add_recording(session, recording_id=recording_id, name=name)
    add_ground_truth(
        session,
        gt_id=f"gt_{recording_id}",
        recording_id=recording_id,
        class_id=9,
        class_name="LoRa 250kHz",
        t0=0.01,
        t1=0.02,
        f0=2_440_600_000.0,
        f1=2_440_700_000.0,
    )


def seed_batch_run(session, *, recording_id: str, run_id: str, item_key: str,
                   fingerprint: str = FINGERPRINT, pipeline_version: str = "1.0",
                   executor: str = "imported", status: str = "completed",
                   recording_manifest_hash: str | None = None):
    add_run(
        session,
        run_id=run_id,
        recording_id=recording_id,
        pipeline_id="pipeline_x",
        pipeline_version=pipeline_version,
        executor=executor,
        status=status,
        parameters_json=batch_parameters(
            item_key, fingerprint=fingerprint, recording_manifest_hash=recording_manifest_hash
        ),
    )
    add_detection(
        session,
        detection_id=f"det_{run_id}",
        run_id=run_id,
        class_id=9,
        class_name="LoRa 250kHz",
        confidence=0.9,
        t0=0.01,
        t1=0.02,
        f0=2_440_600_000.0,
        f1=2_440_700_000.0,
    )


def test_catalog_contains_only_completed_imported_batch_runs(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="a")
        seed_batch_run(session, recording_id="rec_b", run_id="run_b", item_key="b")
        add_run(session, run_id="run_local", recording_id="rec_a", executor="local_cpu")
        add_run(session, run_id="run_plain_import", recording_id="rec_b", executor="imported")
        seed_batch_run(session, recording_id="rec_b", run_id="run_failed", item_key="failed", status="failed")
        session.commit()
        entries = DatasetBenchmarkService(session).list_imported_batches()

    assert len(entries) == 1
    entry = entries[0]
    assert entry.import_fingerprint == FINGERPRINT
    assert entry.run_count == 2
    assert entry.detection_count == 2
    assert entry.ready is True
    assert entry.inconsistency_reasons == ()


def test_resolver_returns_exact_manifest_mapping(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="a")
        seed_batch_run(session, recording_id="rec_b", run_id="run_b", item_key="b")
        session.commit()
        svc = DatasetBenchmarkService(session)
        expected_hash = svc.prepare_manifest("SpaceNet", "test", "spacenet_14").recording_manifest_hash
        preview = svc.resolve_imported_batch(FINGERPRINT)

    assert preview.dataset_name == "SpaceNet"
    assert preview.dataset_split == "test"
    assert preview.label_space == "spacenet_14"
    assert preview.pipeline_id == "pipeline_x"
    assert preview.pipeline_version == "1.0"
    assert preview.recording_manifest_hash == expected_hash
    assert preview.expected_recordings == 2
    assert preview.resolved_recordings == 2
    assert preview.missing_recordings == 0
    assert preview.conflict_count == 0
    assert [(x.recording_id, x.analysis_run_id) for x in preview.entries] == [
        ("rec_a", "run_a"), ("rec_b", "run_b")
    ]


def test_resolver_rejects_duplicate_item_key(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="same")
        seed_batch_run(session, recording_id="rec_b", run_id="run_b", item_key="same")
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_STATE_INCONSISTENT"


def test_resolver_rejects_duplicate_recording_mapping(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a1", item_key="a1")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a2", item_key="a2")
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_STATE_INCONSISTENT"


def test_resolver_rejects_mixed_pipeline(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="a", pipeline_version="1.0")
        seed_batch_run(session, recording_id="rec_b", run_id="run_b", item_key="b", pipeline_version="2.0")
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_STATE_INCONSISTENT"


def test_resolver_rejects_incomplete_current_manifest(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="a")
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_DATASET_INCOMPLETE"


def test_resolver_rejects_present_but_mismatched_manifest_provenance(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        wrong = "e" * 64
        seed_batch_run(
            session, recording_id="rec_a", run_id="run_a", item_key="a",
            recording_manifest_hash=wrong,
        )
        seed_batch_run(
            session, recording_id="rec_b", run_id="run_b", item_key="b",
            recording_manifest_hash=wrong,
        )
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_DATASET_INCOMPLETE"


def test_resolver_not_found(client):
    with client.app.state.database.session_factory() as session:
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch("f" * 64)
    assert exc.value.code == "IMPORTED_BATCH_NOT_FOUND"
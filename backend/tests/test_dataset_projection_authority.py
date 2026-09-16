"""Projection authority: manifest, experiment/evaluation/imported-batch scoping,
and additive migration idempotency (B2, B6, B8, B9, migration)."""
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.benchmarks.service import DatasetBenchmarkService
from app.datasets.projection import DatasetProjectionResolver
from app.db.base import Base
from app.db.migrations import run_additive_migrations, upgrade_v1_1_dataset_projection
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

from benchmark_fixture import add_run


def _add_member(session, root: Path, name: str, *, gt: bool = True, split: str = "test",
                dataset_name: str = "SpaceNet", label_space: str = "spacenet_14") -> RecordingModel:
    data_path = root / split / f"{name}.bin"
    recording = RecordingModel(
        id=f"rec_{root.name}_{name}", name=name, data_path=str(data_path),
        data_format="float16_interleaved_le", source="spacenet", external_path=str(data_path),
        sample_rate_hz=1.0, center_frequency_hz=2.0, frequency_low_hz=1.5,
        frequency_high_hz=2.5, num_samples=1, duration_s=1.0,
        dataset_name=dataset_name, dataset_split=split, label_space=label_space,
        has_ground_truth=gt,
    )
    session.add(recording)
    if gt:
        session.add(GroundTruthModel(
            id=f"gt_{recording.id}", recording_id=recording.id, t_start_s=0.1, t_end_s=0.2,
            f_low_hz=1.6, f_high_hz=1.7, class_id=1, class_name="WiFi",
        ))
    return recording


def _two_roots(session, tmp_path):
    root_a = tmp_path / "SpaceNet-A"
    root_b = tmp_path / "SpaceNet-B"
    a = [_add_member(session, root_a, "a1"), _add_member(session, root_a, "a2")]
    b = [_add_member(session, root_b, "b1"), _add_member(session, root_b, "b2"), _add_member(session, root_b, "b3")]
    session.commit()
    resolver = DatasetProjectionResolver(session)
    pid_a = resolver.find_for_recording(a[0]).dataset_projection_id
    pid_b = resolver.find_for_recording(b[0]).dataset_projection_id
    return pid_a, pid_b, a, b


# ---------- B2: projection manifest ----------

def test_projection_manifest_is_scoped_to_one_root(session, tmp_path):
    pid_a, pid_b, _a, _b = _two_roots(session, tmp_path)
    service = DatasetBenchmarkService(session)
    manifest_a = service.prepare_projection_manifest(pid_a)
    manifest_b = service.prepare_projection_manifest(pid_b)
    assert manifest_a.expected_recordings == 2
    assert manifest_b.expected_recordings == 3
    assert {entry.recording_name for entry in manifest_a.entries} == {"a1", "a2"}
    assert {entry.recording_name for entry in manifest_b.entries} == {"b1", "b2", "b3"}
    assert manifest_a.recording_manifest_hash != manifest_b.recording_manifest_hash


# ---------- Migration ----------

def test_migration_adds_columns_to_legacy_tables_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE dataset_experiments (id VARCHAR(64) PRIMARY KEY, name VARCHAR(255))"))
        connection.execute(text("CREATE TABLE dataset_evaluations (id VARCHAR(64) PRIMARY KEY, name VARCHAR(255))"))
    upgrade_v1_1_dataset_projection(engine)
    upgrade_v1_1_dataset_projection(engine)  # idempotent
    assert "dataset_projection_id" in {c["name"] for c in inspect(engine).get_columns("dataset_experiments")}
    assert "dataset_projection_id" in {c["name"] for c in inspect(engine).get_columns("dataset_evaluations")}
    assert "ix_dataset_experiments_dataset_projection_id" in {
        index["name"] for index in inspect(engine).get_indexes("dataset_experiments")
    }


def test_migration_is_idempotent_on_clean_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'clean.db'}")
    Base.metadata.create_all(engine)
    run_additive_migrations(engine)
    run_additive_migrations(engine)
    assert "dataset_projection_id" in {c["name"] for c in inspect(engine).get_columns("dataset_experiments")}
    assert "dataset_projection_id" in {c["name"] for c in inspect(engine).get_columns("dataset_evaluations")}


# ---------- B6: dataset experiment projection scope ----------

def test_experiment_from_projection_contains_only_root_a(client, session, tmp_path):
    from dataset_experiment_fixtures import local_services

    pid_a, pid_b, _a, _b = _two_roots(session, tmp_path)
    _ds_session, ds, _analysis, _provider = local_services(client)
    experiment = ds.create_experiment(
        name="A experiment",
        dataset_name=None,
        dataset_split=None,
        dataset_label_space=None,
        dataset_projection_id=pid_a,
        plugin_id="g3_local",
        plugin_version="1.0",
        executor="local_cpu",
        parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2",
        max_concurrency=1,
    )
    assert experiment.dataset_projection_id == pid_a
    read = ds.get_experiment(experiment.id)
    assert read.dataset_projection_id == pid_a
    items = ds.list_items(experiment.id)
    member_ids = {recording.id for recording in DatasetProjectionResolver(session).members(pid_a)}
    assert {item.recording_id for item in items} <= member_ids
    # revalidation remains scoped to A
    ds.revalidate_frozen_identity(experiment.id)


# ---------- B8: dataset evaluation projection scope ----------

def test_evaluation_from_projection_is_scoped_and_persisted(session, tmp_path):
    pid_a, pid_b, _a, _b = _two_roots(session, tmp_path)
    service = DatasetBenchmarkService(session)
    manifest = service.prepare_projection_manifest(pid_a)
    items = []
    for entry in manifest.entries:
        add_run(session, run_id=f"run_{entry.recording_id}", recording_id=entry.recording_id,
                pipeline_id="stft_energy_detector", pipeline_version="1.0",
                executor="local_cpu", status="completed")
        items.append({"recording_id": entry.recording_id, "analysis_run_id": f"run_{entry.recording_id}"})
    session.commit()
    evaluation = service.prepare_evaluation(
        name="A eval", dataset_name=None, dataset_split=None, label_space=None,
        recording_manifest_hash=manifest.recording_manifest_hash, items=items,
        dataset_projection_id=pid_a,
    )
    assert evaluation.dataset_projection_id == pid_a
    assert evaluation.expected_recordings == 2


# ---------- B9: imported-batch resolution projection scope ----------

def test_imported_batch_resolves_against_one_root_only(session, tmp_path):
    pid_a, pid_b, a, b = _two_roots(session, tmp_path)
    fingerprint = "a" * 64
    for index, recording in enumerate(a):
        add_run(
            session, run_id=f"run_a_{index}", recording_id=recording.id,
            pipeline_id="zoomspec", pipeline_version="1.0", executor="imported", status="completed",
            parameters_json={"batch_import": {
                "import_fingerprint": fingerprint, "item_key": f"key_{index}",
                "batch_id": "batch_1", "archive_sha256": "b" * 64,
            }},
        )
    session.commit()
    preview = DatasetBenchmarkService(session).resolve_imported_batch(fingerprint)
    assert preview.dataset_projection_id == pid_a
    assert {entry.recording_id for entry in preview.entries} == {recording.id for recording in a}
    assert all(entry.recording_id not in {recording.id for recording in b} for entry in preview.entries)

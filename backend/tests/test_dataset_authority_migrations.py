"""P1 dataset authority: model, additive migration, legacy backfill, idempotence."""
from pathlib import Path

from sqlalchemy import func, inspect, select

from app.db.migrations import run_additive_migrations
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel


def _legacy_member(session, root: Path, name: str, *, gt: bool = True, source: str = "spacenet"):
    split = "test"
    data_path = root / split / f"{name}.bin"
    recording = RecordingModel(
        id=f"rec_{root.name}_{name}", name=name, data_path=str(data_path),
        data_format="float16_interleaved_le", source=source, external_path=str(data_path),
        sample_rate_hz=1.0, center_frequency_hz=2.0, frequency_low_hz=1.5,
        frequency_high_hz=2.5, num_samples=1, duration_s=1.0,
        dataset_name="SpaceNet", dataset_split=split, label_space="spacenet_14",
        has_ground_truth=gt,
    )
    session.add(recording)
    if gt:
        session.add(GroundTruthModel(
            id=f"gt_{recording.id}", recording_id=recording.id, t_start_s=0.1, t_end_s=0.2,
            f_low_hz=1.6, f_high_hz=1.7, class_id=1, class_name="WiFi",
        ))
    return recording


def _standalone(session, name: str):
    session.add(RecordingModel(
        id=f"rec_standalone_{name}", name=name, data_path=f"recordings/rec_{name}/raw.iq",
        data_format="complex64_le", source="custom", external_path=None,
        sample_rate_hz=1.0, center_frequency_hz=0.0, frequency_low_hz=-0.5,
        frequency_high_hz=0.5, num_samples=1, duration_s=1.0,
        dataset_name=None, dataset_split=None, label_space=None, has_ground_truth=False,
    ))


def test_datasets_table_and_recording_columns_exist(session):
    engine = session.get_bind()
    tables = set(inspect(engine).get_table_names())
    assert "datasets" in tables
    dataset_columns = {c["name"] for c in inspect(engine).get_columns("datasets")}
    assert {
        "id", "name", "split", "adapter_id", "label_space", "local_root",
        "portable_fingerprint", "sample_count", "ground_truth_sample_count", "created_at",
    } <= dataset_columns
    recording_columns = {c["name"] for c in inspect(engine).get_columns("recordings")}
    assert {"dataset_id", "sample_key"} <= recording_columns


def test_old_db_gets_datasets_table_and_membership_columns(tmp_path):
    from app.core.config import Settings
    from app.db.base import load_domain_models
    from app.db.session import Database

    settings = Settings(project_root=tmp_path, data_root=tmp_path / "data",
                        label_space_root=tmp_path / "label_spaces",
                        database_url=f"sqlite:///{tmp_path / 'old.db'}")
    load_domain_models()
    database = Database(settings.database_url)
    with database.engine.begin() as connection:
        # A realistic pre-P1 recordings table (all legacy columns, no P1 membership).
        connection.exec_driver_sql(
            "CREATE TABLE recordings ("
            "id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL, "
            "data_path VARCHAR(1024) NOT NULL, data_format VARCHAR(64) NOT NULL, "
            "source VARCHAR(32) NOT NULL DEFAULT 'custom', external_path VARCHAR(1024), "
            "sample_rate_hz FLOAT NOT NULL, center_frequency_hz FLOAT NOT NULL, "
            "frequency_low_hz FLOAT NOT NULL, frequency_high_hz FLOAT NOT NULL, "
            "num_samples INTEGER NOT NULL, duration_s FLOAT NOT NULL, "
            "dataset_name VARCHAR(255), dataset_split VARCHAR(64), label_space VARCHAR(128), "
            "has_ground_truth BOOLEAN NOT NULL DEFAULT 0, source_data_sha256 VARCHAR(64), "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        connection.exec_driver_sql(
            "CREATE TABLE analysis_runs (id VARCHAR(64) PRIMARY KEY, status VARCHAR(32) NOT NULL DEFAULT 'pending')")
    run_additive_migrations(database.engine)
    with database.engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        recordings = {c["name"] for c in inspect(connection).get_columns("recordings")}
    assert "datasets" in tables
    assert {"dataset_id", "sample_key"} <= recordings


def test_migration_preserves_existing_records_and_runs(session):
    from app.analysis.model import AnalysisRunModel

    _legacy_member(session, Path("D:/SpaceNet-Preserve"), "s1")
    _standalone(session, "free")
    session.add(AnalysisRunModel(
        id="run_keep", recording_id="rec_standalone_free", pipeline_id="dummy",
        pipeline_version="1.0", executor="local_cpu", status="completed", parameters_json={},
        execution_metadata_json=None, hardware_info_json=None, started_at=None,
        finished_at=None, error_type=None, error_message=None, worker_pid=None, created_at=None,
    ))
    session.commit()
    before_recordings = int(session.scalar(select(func.count()).select_from(RecordingModel)) or 0)
    before_runs = int(session.scalar(select(func.count()).select_from(AnalysisRunModel)) or 0)

    run_additive_migrations(session.get_bind())
    session.expire_all()

    assert session.get(AnalysisRunModel, "run_keep") is not None
    assert session.get(RecordingModel, "rec_standalone_free").dataset_id is None
    assert int(session.scalar(select(func.count()).select_from(RecordingModel)) or 0) == before_recordings
    assert int(session.scalar(select(func.count()).select_from(AnalysisRunModel)) or 0) == before_runs


def test_backfill_creates_dataset_and_links_members(session):
    from app.datasets.model import DatasetModel

    root = Path("D:/SpaceNet-Backfill")
    a = _legacy_member(session, root, "a")
    b = _legacy_member(session, root, "b", gt=False)
    _standalone(session, "solo")
    session.commit()

    run_additive_migrations(session.get_bind())
    session.expire_all()

    datasets = list(session.scalars(select(DatasetModel)).all())
    assert len(datasets) == 1
    dataset = datasets[0]
    assert dataset.name == "SpaceNet"
    assert dataset.split == "test"
    assert dataset.adapter_id == "spacenet"
    assert dataset.label_space == "spacenet_14"
    assert dataset.sample_count == 2
    assert dataset.ground_truth_sample_count == 1
    assert dataset.portable_fingerprint is not None and len(dataset.portable_fingerprint) == 64

    assert session.get(RecordingModel, a.id).dataset_id == dataset.id
    assert session.get(RecordingModel, a.id).sample_key == "a"
    assert session.get(RecordingModel, b.id).dataset_id == dataset.id
    assert session.get(RecordingModel, b.id).sample_key == "b"
    assert session.get(RecordingModel, "rec_standalone_solo").dataset_id is None


def test_backfill_is_idempotent(session):
    from app.datasets.model import DatasetModel

    root = Path("D:/SpaceNet-Idem")
    _legacy_member(session, root, "x")
    session.commit()

    run_additive_migrations(session.get_bind())
    session.expire_all()
    first_ids = sorted(d.id for d in session.scalars(select(DatasetModel)).all())

    run_additive_migrations(session.get_bind())
    session.expire_all()
    second_ids = sorted(d.id for d in session.scalars(select(DatasetModel)).all())
    assert first_ids == second_ids
    assert len(second_ids) == 1
    dataset = session.get(DatasetModel, second_ids[0])
    assert dataset.sample_count == 1
    assert dataset.portable_fingerprint is not None

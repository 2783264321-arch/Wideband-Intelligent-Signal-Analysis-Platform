"""Small additive migrations for databases created by the V1 core slice."""
from sqlalchemy import inspect, select, text


def upgrade_recording_external(engine) -> None:
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("recordings")}
        if "source" not in columns:
            connection.execute(text("ALTER TABLE recordings ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'custom'"))
        if "external_path" not in columns:
            connection.execute(text("ALTER TABLE recordings ADD COLUMN external_path VARCHAR(1024)"))


def upgrade_dataset_benchmarks(engine) -> None:
    from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel

    DatasetEvaluationModel.__table__.create(engine, checkfirst=True)
    DatasetEvaluationItemModel.__table__.create(engine, checkfirst=True)


def upgrade_m9_1_provenance(engine) -> None:
    with engine.begin() as connection:
        recordings = {column["name"] for column in inspect(connection).get_columns("recordings")}
        if "source_data_sha256" not in recordings:
            connection.execute(text("ALTER TABLE recordings ADD COLUMN source_data_sha256 VARCHAR(64)"))
        analysis_runs = {column["name"] for column in inspect(connection).get_columns("analysis_runs")}
        if "execution_metadata_json" not in analysis_runs:
            connection.execute(text("ALTER TABLE analysis_runs ADD COLUMN execution_metadata_json JSON"))


def upgrade_dataset_experiments(engine) -> None:
    from app.dataset_experiments.model import (
        DatasetExperimentAttemptModel,
        DatasetExperimentItemModel,
        DatasetExperimentModel,
    )

    DatasetExperimentModel.__table__.create(engine, checkfirst=True)
    DatasetExperimentItemModel.__table__.create(engine, checkfirst=True)
    DatasetExperimentAttemptModel.__table__.create(engine, checkfirst=True)


def run_additive_migrations(engine) -> None:
    upgrade_recording_external(engine)
    upgrade_dataset_benchmarks(engine)
    upgrade_m9_1_provenance(engine)
    upgrade_dataset_experiments(engine)
    upgrade_v1_1_dataset_projection(engine)
    upgrade_p1_dataset_authority(engine)
    upgrade_p3_dataset_analysis_authority(engine)


def upgrade_p3_dataset_analysis_authority(engine) -> None:
    """P3: first-class dataset_id on dataset experiments/evaluations + backfill.

    Additive and idempotent. Legacy projection identity columns are preserved.
    Backfill resolves via ``dataset_projection_id`` -> projection members ->
    the shared ``recording.dataset_id``; ambiguous mappings are left NULL.
    """
    from sqlalchemy.orm import Session

    with engine.begin() as connection:
        experiments = {c["name"] for c in inspect(connection).get_columns("dataset_experiments")}
        if "dataset_id" not in experiments:
            connection.execute(text("ALTER TABLE dataset_experiments ADD COLUMN dataset_id VARCHAR(64)"))
        evaluations = {c["name"] for c in inspect(connection).get_columns("dataset_evaluations")}
        if "dataset_id" not in evaluations:
            connection.execute(text("ALTER TABLE dataset_evaluations ADD COLUMN dataset_id VARCHAR(64)"))
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_dataset_experiments_dataset_id ON dataset_experiments (dataset_id)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_dataset_evaluations_dataset_id ON dataset_evaluations (dataset_id)")
        )

    from app.benchmarks.model import DatasetEvaluationModel
    from app.dataset_experiments.model import DatasetExperimentModel
    from app.datasets.projection import DatasetProjectionResolver

    with Session(engine) as session:
        def _resolve(projection_id: str) -> str | None:
            members = DatasetProjectionResolver(session).members(projection_id)
            dataset_ids = {member.dataset_id for member in members if member.dataset_id is not None}
            return next(iter(dataset_ids)) if len(dataset_ids) == 1 else None

        for model in (DatasetExperimentModel, DatasetEvaluationModel):
            rows = list(
                session.scalars(
                    select(model).where(
                        model.dataset_id.is_(None), model.dataset_projection_id.is_not(None)
                    )
                ).all()
            )
            for row in rows:
                resolved = _resolve(row.dataset_projection_id)
                if resolved is not None:
                    row.dataset_id = resolved
        session.commit()


def upgrade_p1_dataset_authority(engine) -> None:
    """P1: first-class datasets table + recordings membership + legacy backfill.

    Additive and idempotent: existing recordings, analysis runs, ground truth and
    detections are preserved; no raw IQ is copied and external_path is untouched.
    """
    from sqlalchemy.orm import Session

    from app.datasets.backfill import backfill_legacy_datasets
    from app.datasets.model import DatasetModel
    from app.recordings.model import RecordingModel

    DatasetModel.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        recordings = {column["name"] for column in inspect(connection).get_columns("recordings")}
        if "dataset_id" not in recordings:
            connection.execute(text("ALTER TABLE recordings ADD COLUMN dataset_id VARCHAR(64)"))
        if "sample_key" not in recordings:
            connection.execute(text("ALTER TABLE recordings ADD COLUMN sample_key VARCHAR(255)"))
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_recordings_dataset_id ON recordings (dataset_id)")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_datasets_portable_fingerprint "
                "ON datasets (portable_fingerprint)"
            )
        )
        recording_columns = {
            column["name"] for column in inspect(connection).get_columns("recordings")
        }

    # Backfill only when the recordings table has the full domain schema. Some
    # partial/foreign test schemas intentionally contain a subset of columns and
    # must not be treated as legacy dataset rows.
    expected_columns = {column.name for column in RecordingModel.__table__.columns}
    if expected_columns <= recording_columns:
        with Session(engine) as session:
            backfill_legacy_datasets(session)
            session.commit()


def upgrade_v1_1_dataset_projection(engine) -> None:
    """V1.1: optional opaque dataset projection identity on experiments/evaluations."""
    with engine.begin() as connection:
        experiments = {column["name"] for column in inspect(connection).get_columns("dataset_experiments")}
        if "dataset_projection_id" not in experiments:
            connection.execute(
                text("ALTER TABLE dataset_experiments ADD COLUMN dataset_projection_id VARCHAR(64)")
            )
        evaluations = {column["name"] for column in inspect(connection).get_columns("dataset_evaluations")}
        if "dataset_projection_id" not in evaluations:
            connection.execute(
                text("ALTER TABLE dataset_evaluations ADD COLUMN dataset_projection_id VARCHAR(64)")
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_dataset_experiments_dataset_projection_id "
                "ON dataset_experiments (dataset_projection_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_dataset_evaluations_dataset_projection_id "
                "ON dataset_evaluations (dataset_projection_id)"
            )
        )
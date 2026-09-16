"""Small additive migrations for databases created by the V1 core slice."""
from sqlalchemy import inspect, text


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
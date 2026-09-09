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


def run_additive_migrations(engine) -> None:
    upgrade_recording_external(engine)
    upgrade_dataset_benchmarks(engine)
    upgrade_m9_1_provenance(engine)
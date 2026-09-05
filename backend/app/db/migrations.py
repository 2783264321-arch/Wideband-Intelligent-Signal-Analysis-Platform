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


def run_additive_migrations(engine) -> None:
    upgrade_recording_external(engine)
    upgrade_dataset_benchmarks(engine)
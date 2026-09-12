from sqlalchemy import inspect

from app.analysis.model import AnalysisRunModel
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database

_NEW_TABLES = {"dataset_experiments", "dataset_experiment_items", "dataset_experiment_attempts"}
_ANALYSIS_RUN_COLUMNS = {
    "id", "recording_id", "pipeline_id", "pipeline_version", "executor", "status",
    "parameters_json", "execution_metadata_json", "hardware_info_json", "started_at",
    "finished_at", "error_type", "error_message", "worker_pid", "created_at",
}


def _fresh_database(settings) -> Database:
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    return database


def test_three_tables_are_registered_and_created(settings):
    database = _fresh_database(settings)
    names = set(inspect(database.engine).get_table_names())
    assert _NEW_TABLES <= names


def test_additive_migration_creates_tables_for_existing_v1_database(settings):
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.tables["recordings"].create(database.engine, checkfirst=True)
    Base.metadata.tables["analysis_runs"].create(database.engine, checkfirst=True)
    run_additive_migrations(database.engine)
    names = set(inspect(database.engine).get_table_names())
    assert _NEW_TABLES <= names


def test_additive_migration_is_idempotent(settings):
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.tables["recordings"].create(database.engine, checkfirst=True)
    Base.metadata.tables["analysis_runs"].create(database.engine, checkfirst=True)
    run_additive_migrations(database.engine)
    run_additive_migrations(database.engine)  # must not raise
    names = set(inspect(database.engine).get_table_names())
    assert _NEW_TABLES <= names


def test_foreign_keys_exist(settings):
    database = _fresh_database(settings)
    inspector = inspect(database.engine)

    def targets(table):
        return {
            (fk["constrained_columns"][0], fk["referred_table"])
            for fk in inspector.get_foreign_keys(table)
        }

    assert ("experiment_id", "dataset_experiments") in targets("dataset_experiment_items")
    assert ("recording_id", "recordings") in targets("dataset_experiment_items")
    assert ("experiment_item_id", "dataset_experiment_items") in targets("dataset_experiment_attempts")
    assert ("analysis_run_id", "analysis_runs") in targets("dataset_experiment_attempts")
    assert ("dataset_evaluation_id", "dataset_evaluations") in targets("dataset_experiments")


def test_item_and_attempt_unique_constraints_exist(settings):
    database = _fresh_database(settings)
    inspector = inspect(database.engine)

    item_columns = {
        tuple(c["column_names"]) for c in inspector.get_unique_constraints("dataset_experiment_items")
    }
    attempt_columns = {
        tuple(c["column_names"]) for c in inspector.get_unique_constraints("dataset_experiment_attempts")
    }
    assert ("experiment_id", "recording_id") in item_columns
    assert ("experiment_id", "manifest_order") in item_columns
    assert ("experiment_item_id", "attempt_number") in attempt_columns
    assert ("analysis_run_id",) in attempt_columns


def test_analysis_run_schema_unchanged(settings):
    database = _fresh_database(settings)
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert not any("experiment" in column for column in AnalysisRunModel.__table__.columns.keys())


def test_no_persisted_aggregate_or_derived_authority_columns(settings):
    database = _fresh_database(settings)
    inspector = inspect(database.engine)
    experiment_columns = {c["name"] for c in inspector.get_columns("dataset_experiments")}
    item_columns = {c["name"] for c in inspector.get_columns("dataset_experiment_items")}
    assert experiment_columns.isdisjoint(
        {"queued_items", "running_items", "completed_items", "failed_items", "attempt_count"}
    )
    assert item_columns.isdisjoint({"current_analysis_run_id", "attempt_count"})

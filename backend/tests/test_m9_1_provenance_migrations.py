from sqlalchemy import inspect

from app.analysis.model import AnalysisRunModel
from app.db.migrations import run_additive_migrations
from app.recordings.model import RecordingModel


def test_columns_exist_on_fresh_db(session):
    engine = session.get_bind()
    recordings = {c["name"] for c in inspect(engine).get_columns("recordings")}
    analysis_runs = {c["name"] for c in inspect(engine).get_columns("analysis_runs")}
    assert "source_data_sha256" in recordings
    assert "execution_metadata_json" in analysis_runs


def test_old_db_gets_columns_after_additive_migration(tmp_path):
    from app.core.config import Settings
    from app.db.base import load_domain_models
    from app.db.session import Database

    settings = Settings(project_root=tmp_path, data_root=tmp_path / "data",
                        label_space_root=tmp_path / "label_spaces",
                        database_url=f"sqlite:///{tmp_path / 'old.db'}")
    load_domain_models()
    db = Database(settings.database_url)
    # Build a minimal pre-M9.1 DB directly with exec_driver_sql. Keep the tables
    # that run_additive_migrations() inspects/alters so it can execute successfully.
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE recordings (id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL)")
        connection.exec_driver_sql(
            "CREATE TABLE analysis_runs (id VARCHAR(64) PRIMARY KEY, status VARCHAR(32) NOT NULL DEFAULT 'pending')")
    run_additive_migrations(db.engine)
    with db.engine.begin() as connection:
        recordings = {c["name"] for c in inspect(connection).get_columns("recordings")}
        analysis_runs = {c["name"] for c in inspect(connection).get_columns("analysis_runs")}
    assert "source_data_sha256" in recordings
    assert "execution_metadata_json" in analysis_runs


def test_read_schemas_expose_new_fields(session):
    from app.analysis.schema import AnalysisRunRead
    from app.recordings.schema import RecordingRead

    recording_fields = RecordingRead.model_fields
    run_fields = AnalysisRunRead.model_fields
    assert "source_data_sha256" in recording_fields
    assert "execution_metadata_json" in run_fields
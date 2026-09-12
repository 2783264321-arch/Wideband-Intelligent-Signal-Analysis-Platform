from app.analysis.model import AnalysisRunModel
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)

_ANALYSIS_RUN_COLUMNS = {
    "id", "recording_id", "pipeline_id", "pipeline_version", "executor", "status",
    "parameters_json", "execution_metadata_json", "hardware_info_json", "started_at",
    "finished_at", "error_type", "error_message", "worker_pid", "created_at",
}
_ATTEMPT_COLUMNS = {
    "id", "experiment_item_id", "attempt_number", "analysis_run_id",
    "launch_requested_at", "created_at",
}


def test_analysis_run_schema_unchanged_by_g3():
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert "launch_requested_at" not in AnalysisRunModel.__table__.columns.keys()


def test_attempt_schema_unchanged_by_g3():
    assert set(DatasetExperimentAttemptModel.__table__.columns.keys()) == _ATTEMPT_COLUMNS
    assert "attempt_count" not in DatasetExperimentAttemptModel.__table__.columns.keys()
    assert "attempt_count" not in DatasetExperimentItemModel.__table__.columns.keys()

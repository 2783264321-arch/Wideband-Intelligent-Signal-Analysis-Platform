import inspect

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
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


def test_analysis_run_schema_unchanged_by_g3b():
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert "launch_requested_at" not in AnalysisRunModel.__table__.columns.keys()


def test_attempt_schema_unchanged_by_g3b():
    assert set(DatasetExperimentAttemptModel.__table__.columns.keys()) == _ATTEMPT_COLUMNS


def test_launch_prepared_run_docstring_numbering_is_current():
    doc = AnalysisService.launch_prepared_run.__doc__
    assert "G3-A Transaction A COMMIT" in doc
    assert "G3-B Transaction B COMMIT" in doc
    assert "G4 Transaction B COMMIT" not in doc


def test_dataset_experiment_service_does_not_call_provider_launch_directly():
    from app.dataset_experiments import service as dataset_service

    source = inspect.getsource(dataset_service)
    assert "provider.launch(" not in source
    assert "launch_prepared_run(" in source


def test_remote_recovery_does_not_interpret_launch_requested_at():
    from app.remote_execution import coordinator, recovery

    assert "launch_requested_at" not in inspect.getsource(recovery)
    assert "launch_requested_at" not in inspect.getsource(coordinator)

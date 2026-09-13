import inspect

from app.analysis.model import AnalysisRunModel
from app.dataset_experiments import coordinator as coordinator_module
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)

_EXPERIMENT_COLUMNS = {
    "id", "name", "dataset_name", "dataset_split", "dataset_label_space",
    "recording_manifest_hash", "plugin_id", "plugin_version", "model_release_id",
    "asset_manifest_sha256", "parameters_json", "executor", "runtime_descriptor_json",
    "evaluation_protocol", "max_concurrency", "status", "dataset_evaluation_id",
    "coordinator_token", "worker_pid", "heartbeat_at", "error_type", "error_message",
    "created_at", "started_at", "completed_at",
}
_ITEM_COLUMNS = {
    "id", "experiment_id", "manifest_order", "recording_id", "status",
    "last_error_type", "last_error_message", "created_at", "updated_at",
}
_ATTEMPT_COLUMNS = {
    "id", "experiment_item_id", "attempt_number", "analysis_run_id",
    "launch_requested_at", "created_at",
}
_ANALYSIS_RUN_COLUMNS = {
    "id", "recording_id", "pipeline_id", "pipeline_version", "executor", "status",
    "parameters_json", "execution_metadata_json", "hardware_info_json", "started_at",
    "finished_at", "error_type", "error_message", "worker_pid", "created_at",
}


def test_dataset_experiment_schemas_unchanged_by_g3c():
    assert set(DatasetExperimentModel.__table__.columns.keys()) == _EXPERIMENT_COLUMNS
    assert set(DatasetExperimentItemModel.__table__.columns.keys()) == _ITEM_COLUMNS
    assert set(DatasetExperimentAttemptModel.__table__.columns.keys()) == _ATTEMPT_COLUMNS
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS


def test_coordinator_has_no_direct_execution_or_g5_behavior():
    source = inspect.getsource(coordinator_module)
    assert "prepare_run(" not in source
    assert "provider.launch(" not in source
    assert "DatasetEvaluation" not in source
    assert 'status="evaluating"' not in source
    assert "status = \"evaluating\"" not in source
    assert 'status="completed"' not in source
    assert "rotate_coordinator_token" not in source

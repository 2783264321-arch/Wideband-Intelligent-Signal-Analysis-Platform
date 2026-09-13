import inspect
import subprocess
import sys
from pathlib import Path

from app.analysis.model import AnalysisRunModel
from app.dataset_experiments import coordinator as coordinator_module
from app.dataset_experiments import recovery as recovery_module
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]

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


def test_g4_schemas_unchanged():
    assert set(DatasetExperimentModel.__table__.columns.keys()) == _EXPERIMENT_COLUMNS
    assert set(DatasetExperimentItemModel.__table__.columns.keys()) == _ITEM_COLUMNS
    assert set(DatasetExperimentAttemptModel.__table__.columns.keys()) == _ATTEMPT_COLUMNS
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS


def test_recovery_module_boundaries():
    source = inspect.getsource(recovery_module)
    assert "remote_execution" not in source
    assert "prepare_run(" not in source
    assert "provider.launch(" not in source
    assert "DatasetEvaluation" not in source
    assert "paramiko" not in source
    assert "startup_recovery_cutoff" in source
    assert "exists()" in source
    assert recovery_module._ACTIVE_RECOVERY_STATUSES == ("running",)


def test_coordinator_unchanged_by_g4():
    source = inspect.getsource(coordinator_module)
    assert "prepare_run(" not in source
    assert "provider.launch(" not in source
    assert "DatasetEvaluation" not in source
    assert "rotate_coordinator_token" not in source
    assert 'status="evaluating"' not in source


def test_recovery_module_is_torch_free():
    code = (
        "import sys; "
        "import app.dataset_experiments.recovery; "
        "assert 'torch' not in sys.modules, 'torch leaked into recovery'; "
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked into recovery'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BACKEND_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout

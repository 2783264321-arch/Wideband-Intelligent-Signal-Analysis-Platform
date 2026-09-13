import inspect
import subprocess
import sys
from pathlib import Path

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.benchmarks import service as benchmark_service_module
from app.dataset_experiments import coordinator as coordinator_module
from app.dataset_experiments import recovery as recovery_module
from app.dataset_experiments import router as router_module
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
_EVALUATION_COLUMNS = {
    "id", "name", "dataset_name", "dataset_split", "label_space", "pipeline_id",
    "pipeline_version", "status", "expected_recordings", "evaluated_recordings",
    "missing_recordings", "coverage", "comparable", "recording_manifest_hash",
    "evaluation_protocol", "protocol_config_json", "aggregate_metrics_json",
    "per_class_metrics_json", "confusion_json", "progress_stage", "progress_current",
    "progress_total", "worker_pid", "error_type", "error_message", "created_at",
    "started_at", "completed_at",
}
_EVALUATION_ITEM_COLUMNS = {
    "id", "evaluation_id", "manifest_order", "recording_id", "analysis_run_id",
    "status", "gt_count", "prediction_count", "error_reason",
}


def test_g5_schemas_unchanged():
    assert set(DatasetExperimentModel.__table__.columns.keys()) == _EXPERIMENT_COLUMNS
    assert set(DatasetExperimentItemModel.__table__.columns.keys()) == _ITEM_COLUMNS
    assert set(DatasetExperimentAttemptModel.__table__.columns.keys()) == _ATTEMPT_COLUMNS
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert set(DatasetEvaluationModel.__table__.columns.keys()) == _EVALUATION_COLUMNS
    assert set(DatasetEvaluationItemModel.__table__.columns.keys()) == _EVALUATION_ITEM_COLUMNS


def test_recovery_and_coordinator_boundaries():
    for module in (recovery_module, coordinator_module):
        source = inspect.getsource(module)
        assert "remote_execution" not in source
        assert "prepare_run(" not in source
        assert "provider.launch(" not in source
        assert "paramiko" not in source
    assert recovery_module._ACTIVE_RECOVERY_STATUSES == ("running", "evaluating")


def test_benchmark_neutral_seams_and_managed_guard():
    source = inspect.getsource(benchmark_service_module)
    assert "def prepare_evaluation(" in source
    assert "def prepare_retry_evaluation(" in source
    assert "def restore_retry_evaluation(" in source
    assert "_evaluation_is_dataset_experiment_managed" in source
    # The neutral seam must NOT contain the managed-guard rejection.
    neutral = source.split("def prepare_retry_evaluation(")[1].split("def restore_retry_evaluation(")[0]
    assert "BENCHMARK_MANAGED_BY_DATASET_EXPERIMENT" not in neutral


def test_router_exposes_exact_v1_surface():
    routes: dict[str, set] = {}
    for route in router_module.router.routes:
        routes.setdefault(route.path, set()).update(route.methods or set())
    expected_paths = {
        "/api/dataset-experiments",
        "/api/dataset-experiments/{experiment_id}",
        "/api/dataset-experiments/{experiment_id}/items",
        "/api/dataset-experiments/{experiment_id}/items/{item_id}/attempts",
        "/api/dataset-experiments/{experiment_id}/run",
        "/api/dataset-experiments/{experiment_id}/retry-failed",
        "/api/dataset-experiments/{experiment_id}/retry-evaluation",
    }
    assert set(routes) == expected_paths
    required = [
        ("/api/dataset-experiments", "POST"),
        ("/api/dataset-experiments", "GET"),
        ("/api/dataset-experiments/{experiment_id}", "GET"),
        ("/api/dataset-experiments/{experiment_id}/items", "GET"),
        ("/api/dataset-experiments/{experiment_id}/items/{item_id}/attempts", "GET"),
        ("/api/dataset-experiments/{experiment_id}/run", "POST"),
        ("/api/dataset-experiments/{experiment_id}/retry-failed", "POST"),
        ("/api/dataset-experiments/{experiment_id}/retry-evaluation", "POST"),
    ]
    for path, method in required:
        assert method in routes[path]


def test_g5_control_plane_is_torch_free():
    code = (
        "import sys; "
        "import app.dataset_experiments.recovery; "
        "import app.dataset_experiments.coordinator; "
        "import app.dataset_experiments.router; "
        "assert 'torch' not in sys.modules, 'torch leaked'; "
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked'; "
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

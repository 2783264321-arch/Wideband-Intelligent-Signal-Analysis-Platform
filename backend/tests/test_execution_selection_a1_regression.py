"""A1 regression + ML-free guard for the Auto execution integration.

Proves Auto integration does not weaken A1 recovery/fencing and that the
control-plane stays ML-free. Introduces no production code.
"""
import ast
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.dataset_experiments import recovery as dataset_recovery
from app.dataset_experiments.coordinator import is_experiment_level
from app.dataset_experiments.model import DatasetExperimentItemModel, DatasetExperimentModel
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution import recovery as remote_recovery
from app.remote_execution.runtime import (
    FROZEN_AUTHORITY_ITEM_CODES,
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
)

from executor_fixtures import FakeProvider

_DEVICE = {"local_cpu": ("cpu", "float32"), "local_gpu": ("cuda", "float16")}
PLUGIN_ID = "a1_guard"


class GuardPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=PLUGIN_ID, name="Guard", version="1.0", label_space="spacenet_14",
            recommended_device="CPU", cpu_supported=True, stages=(), inspectable_stages=(),
            task_capability="classification", executors_supported=("local_cpu", "local_gpu"),
            recommended_executor="local_cpu",
            technical_execution_capabilities=(
                ExecutionCapability("local_cpu", "cpu", "float32"),
                ExecutionCapability("local_gpu", "cuda", "float16"),
            ),
            parameter_schema={},
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


def _provider(executor):
    device_type, precision = _DEVICE[executor]
    return FakeProvider(executor, device_type=device_type, precision=precision)


def _cert(executor):
    device_type, precision = _DEVICE[executor]
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID, plugin_version="1.0", model_release_id=None,
        executor=executor, device_type=device_type, precision=precision,
        runtime_ref=f"fake:{executor}", evidence_ref="test",
    )


def _registry():
    return ExecutorRegistry(
        {"local_cpu": _provider("local_cpu"), "local_gpu": _provider("local_gpu")},
        ExecutionCertificateStore([_cert("local_cpu"), _cert("local_gpu")]),
    )


def _add_recording(client, *, recording_id="rec_x"):
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=recording_id, name="0", data_path="recordings/0/raw.iq",
            data_format="complex64_le", sample_rate_hz=1e6, center_frequency_hz=0.0,
            frequency_low_hz=-5e5, frequency_high_hz=5e5, num_samples=1000, duration_s=0.001,
            dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            source_data_sha256="1" * 64,
        ))
        session.commit()


def _seed_dataset(client):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_0", name="name_0")
        add_ground_truth(session, gt_id="gt_0", recording_id="rec_0", class_id=9,
                         class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                         f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


# --- A1 invariants ---------------------------------------------------------

def test_frozen_authority_item_codes_unchanged():
    assert tuple(FROZEN_AUTHORITY_ITEM_CODES) == (
        "EXECUTION_CAPABILITY_UNAVAILABLE",
        "EXECUTION_NOT_CERTIFIED",
    )


def test_runtime_descriptor_invalid_is_experiment_level():
    assert is_experiment_level(PlatformError("RUNTIME_DESCRIPTOR_INVALID", "x")) is True
    assert is_experiment_level(PlatformError("EXECUTION_CAPABILITY_UNAVAILABLE", "x")) is False
    assert is_experiment_level(PlatformError("EXECUTION_NOT_CERTIFIED", "x")) is False


def test_recovery_paths_do_not_reference_auto_resolver():
    for module in (dataset_recovery, remote_recovery):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "resolve_auto_execution" not in source


def test_only_create_paths_reference_auto_resolver():
    # The Auto resolver is referenced only by the two create paths and the
    # selection package itself; never by recovery/worker paths.
    allowed = {"analysis/service.py", "dataset_experiments/service.py",
               "execution_selection/resolver.py", "execution_selection/router.py"}
    backend_app = pathlib.Path(dataset_recovery.__file__).resolve().parents[1]
    referenced = {
        str(path.relative_to(backend_app)).replace("\\", "/")
        for path in backend_app.rglob("*.py")
        if "resolve_auto_execution" in path.read_text(encoding="utf-8")
    }
    assert referenced == allowed


# --- frozen executor is used, Auto is never re-resolved --------------------

def test_auto_selected_executor_is_the_launched_executor(client):
    _add_recording(client)
    session = client.app.state.database.session_factory()
    service = AnalysisService(
        session, PipelineRegistry([GuardPipeline()]), client.app.state.job_manager,
        executor_registry=_registry(),
    )
    run = service.create_run(recording_id="rec_x", pipeline_id=PLUGIN_ID,
                             parameters={}, execution_mode="auto")
    provider = service.executor_registry.provider(run.executor)
    assert run.executor == "local_cpu"
    assert provider.launches == [(run.id, None)]


def test_retry_failed_keeps_frozen_executor_without_auto(client, monkeypatch):
    import app.dataset_experiments.service as service_module

    _seed_dataset(client)
    session = client.app.state.database.session_factory()
    service = DatasetExperimentService(
        session, PipelineRegistry([GuardPipeline()]), None, _registry()
    )
    experiment = service.create_experiment(
        name="guard", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PLUGIN_ID, plugin_version="1.0",
        parameters={}, evaluation_protocol="physical_tf_detection_ap_v2",
        max_concurrency=1, execution_mode="auto",
    )
    with client.app.state.database.session_factory() as other:
        stored = other.get(DatasetExperimentModel, experiment.id)
        stored.status = "completed_with_failures"
        stored.completed_at = datetime.now(timezone.utc)
        item = other.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id
        ).first()
        item.status = "failed"
        item.last_error_type = "ANALYSIS_FAILED"
        other.commit()

    def _boom(*args, **kwargs):
        raise AssertionError("retry must never re-resolve Auto")

    monkeypatch.setattr(service_module, "resolve_auto_execution", _boom)
    session.expire_all()
    retried = service.retry_failed(experiment.id, SimpleNamespace(start=lambda *a: 4242))
    assert retried.executor == "local_cpu"

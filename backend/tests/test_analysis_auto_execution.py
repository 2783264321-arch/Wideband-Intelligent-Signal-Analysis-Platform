import ast
import pathlib
from types import SimpleNamespace

import pytest

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.dataset_experiments.service import DatasetExperimentService  # noqa: F401 (import sanity)
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
)

from executor_fixtures import FakeProvider

_DEVICE = {
    "local_cpu": ("cpu", "float32"),
    "local_gpu": ("cuda", "float16"),
    "remote_gpu": ("cuda", "float16"),
}
PLUGIN_ID = "auto_local"
MANIFEST_SHA = "b" * 64


class AutoLocalPipeline(Pipeline):
    def __init__(self, *, release_required=False):
        self._release_required = release_required

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=PLUGIN_ID,
            name="Auto Local",
            version="1.0",
            label_space="spacenet_14",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            task_capability="classification",
            executors_supported=("local_cpu", "local_gpu"),
            recommended_executor="local_cpu",
            technical_execution_capabilities=(
                ExecutionCapability("local_cpu", "cpu", "float32"),
                ExecutionCapability("local_gpu", "cuda", "float16"),
            ),
            parameter_schema={},
            model_release_required=self._release_required,
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class FakeReleaseStore:
    def __init__(self, *, release_id="golden"):
        self.release_id = release_id
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release = SimpleNamespace(
            plugin_id=plugin_id, plugin_version=plugin_version,
            model_release_id=requested or self.release_id,
            asset_manifest_sha256=MANIFEST_SHA,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=MANIFEST_SHA)
        return ResolvedModelRelease(release=release, manifest=manifest)


def _provider(executor, *, available=True, reason_code=None):
    device_type, precision = _DEVICE[executor]
    return FakeProvider(
        executor, device_type=device_type, precision=precision,
        available=available, reason_code=reason_code,
    )


def _cert(executor, *, model_release_id=None):
    device_type, precision = _DEVICE[executor]
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID, plugin_version="1.0", model_release_id=model_release_id,
        executor=executor, device_type=device_type, precision=precision,
        runtime_ref=f"fake:{executor}", evidence_ref="test",
    )


def _registry(providers, certs):
    return ExecutorRegistry(dict(providers), ExecutionCertificateStore(list(certs)))


def _add_recording(client, *, recording_id="rec_x", label_space="spacenet_14",
                   num_samples=1000, duration_s=0.001):
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=recording_id, name="0", data_path="recordings/0/raw.iq",
            data_format="complex64_le", sample_rate_hz=1e6, center_frequency_hz=0.0,
            frequency_low_hz=-5e5, frequency_high_hz=5e5, num_samples=num_samples,
            duration_s=duration_s, dataset_name="SpaceNet", dataset_split="test",
            label_space=label_space, source_data_sha256="1" * 64,
        ))
        session.commit()


def _service(client, *, pipeline=None, store=None, registry=None):
    session = client.app.state.database.session_factory()
    return AnalysisService(
        session,
        PipelineRegistry([pipeline or AutoLocalPipeline()]),
        client.app.state.job_manager,
        model_release_store=store,
        executor_registry=registry or _registry(
            {"local_cpu": _provider("local_cpu"), "local_gpu": _provider("local_gpu")},
            [_cert("local_cpu"), _cert("local_gpu")],
        ),
    )


def _create(service, **overrides):
    kwargs = dict(recording_id="rec_x", pipeline_id=PLUGIN_ID, parameters={})
    kwargs.update(overrides)
    return service.create_run(**kwargs)


# 1. manual + omitted executor -> historical local_cpu
def test_manual_omitted_executor_defaults_to_local_cpu(client):
    _add_recording(client)
    service = _service(client)
    run = _create(service)
    assert run.executor == "local_cpu"
    assert run.execution_metadata_json["requested_execution_mode"] == "manual"


# 2. manual + explicit executor -> exact (unchanged)
def test_manual_explicit_executor_is_exact(client):
    _add_recording(client)
    service = _service(client)
    run = _create(service, executor="local_gpu")
    assert run.executor == "local_gpu"


# manual mode never invokes the Auto resolver
def test_manual_never_invokes_auto_resolver(client, monkeypatch):
    import app.analysis.service as service_module

    def _boom(*args, **kwargs):
        raise AssertionError("manual mode must not invoke Auto resolution")

    monkeypatch.setattr(service_module, "resolve_auto_execution", _boom)
    _add_recording(client)
    service = _service(client)
    run = _create(service, executor="local_cpu")
    assert run.executor == "local_cpu"


# 3. auto + omitted -> resolves exact executor + provenance
def test_auto_resolves_exact_executor_and_persists_provenance(client):
    _add_recording(client)
    service = _service(client)
    run = _create(service, execution_mode="auto")
    assert run.executor == "local_cpu"
    assert run.executor != "auto"
    metadata = dict(run.execution_metadata_json)
    assert metadata["requested_execution_mode"] == "auto"
    assert metadata["auto_reason_code"] == "AUTO_LOCAL_CPU_PREFERRED"
    assert metadata["workload_class"] == "SMALL"
    assert metadata["auto_reason"] == "Local CPU was selected."


# 4. auto + explicit executor -> conflict, no persistence
def test_auto_with_explicit_executor_is_rejected(client):
    _add_recording(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, execution_mode="auto", executor="local_cpu")
    assert exc.value.code == "EXECUTION_REQUEST_INVALID"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


# 5. auto + zero runnable -> fail closed, no persistence
def test_auto_zero_runnable_persists_nothing(client):
    _add_recording(client)
    registry = _registry(
        {"local_cpu": _provider("local_cpu", available=False,
                                reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")},
        [_cert("local_cpu")],
    )
    service = _service(client, registry=registry)
    with pytest.raises(PlatformError) as exc:
        _create(service, execution_mode="auto")
    assert exc.value.code == "AUTO_NO_RUNNABLE_EXECUTOR"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


# 6. auto freezes the exact resolved ModelRelease used for selection
def test_auto_freezes_exact_resolved_release(client):
    _add_recording(client)
    store = FakeReleaseStore(release_id="golden")
    registry = _registry({"local_cpu": _provider("local_cpu")},
                         [_cert("local_cpu", model_release_id="golden")])
    service = _service(client, pipeline=AutoLocalPipeline(release_required=True),
                       store=store, registry=registry)
    run = _create(service, execution_mode="auto")
    assert run.executor == "local_cpu"
    assert run.execution_metadata_json["model_release_id"] == "golden"
    # The final (prepare) resolution must use the exact frozen id, not re-default to None.
    assert store.resolve_calls[-1] == (PLUGIN_ID, "1.0", "golden")


# 7. public read model exposes safe provenance and no private path
def test_public_read_model_exposes_safe_provenance(client):
    _add_recording(client)
    service = _service(client)
    run = _create(service, execution_mode="auto")
    response = client.get(f"/api/analysis-runs/{run.id}")
    assert response.status_code == 200
    body = response.json()
    exposed = body["execution_metadata_json"]
    assert exposed["requested_execution_mode"] == "auto"
    assert exposed["auto_reason_code"] == "AUTO_LOCAL_CPU_PREFERRED"
    assert exposed["workload_class"] == "SMALL"
    raw = response.text
    assert "environment_ref" not in raw
    assert "/fake/" not in raw


# 8. unknown execution_mode -> fail closed
def test_unknown_execution_mode_is_rejected(client):
    _add_recording(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, execution_mode="turbo")
    assert exc.value.code == "EXECUTION_REQUEST_INVALID"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_analysis_service_module_is_ml_free():
    import app.analysis.service as service_module

    source = pathlib.Path(service_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "torch" not in imported
    assert "ultralytics" not in imported

from types import SimpleNamespace

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.schema import ExecutorAvailabilityRead
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
}
PLUGIN_ID = "auto_api"
MANIFEST_SHA = "b" * 64
_REASON_CODES = {
    "AUTO_NO_RUNNABLE_EXECUTOR", "AUTO_ONLY_RUNNABLE_EXECUTOR",
    "AUTO_LOCAL_CPU_PREFERRED", "AUTO_LOCAL_GPU_PREFERRED",
    "AUTO_REMOTE_GPU_PREFERRED", "AUTO_UNKNOWN_RECOMMENDED_EXECUTOR",
    "AUTO_UNKNOWN_DETERMINISTIC_RANK",
}


class AutoApiPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=PLUGIN_ID, name="Auto Api", version="1.0", label_space="spacenet_14",
            recommended_device="CPU", cpu_supported=True, stages=(), inspectable_stages=(),
            task_capability="classification",
            executors_supported=("local_cpu", "local_gpu"),
            recommended_executor="local_cpu",
            technical_execution_capabilities=(
                ExecutionCapability("local_cpu", "cpu", "float32"),
                ExecutionCapability("local_gpu", "cuda", "float16"),
            ),
            parameter_schema={},
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class _MaliciousProbe:
    def availability(self, recording, definition, source_data_sha256, model_release=None):
        return ExecutorAvailabilityRead(
            executor="local_cpu", available=False,
            reason_code="EXECUTION_CAPABILITY_UNAVAILABLE",
            reason_message=r"C:\secret\runtime\python.exe /root/private/model.pt",
            remote_profile=None, recommended=False,
        )


class FakeReleaseStore:
    def resolve(self, plugin_id, plugin_version, requested):
        release = SimpleNamespace(model_release_id=requested or "golden",
                                  asset_manifest_sha256=MANIFEST_SHA)
        manifest = SimpleNamespace(asset_manifest_sha256=MANIFEST_SHA)
        return ResolvedModelRelease(release=release, manifest=manifest)


def _provider(executor, *, available=True, reason_code=None, probe=None):
    device_type, precision = _DEVICE[executor]
    return FakeProvider(
        executor, device_type=device_type, precision=precision,
        available=available, reason_code=reason_code, probe=probe,
    )


def _cert(executor):
    device_type, precision = _DEVICE[executor]
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID, plugin_version="1.0", model_release_id=None,
        executor=executor, device_type=device_type, precision=precision,
        runtime_ref=f"fake:{executor}", evidence_ref="test",
    )


def _install(client, *, providers, certs):
    client.app.state.pipeline_registry = PipelineRegistry([AutoApiPipeline()])
    client.app.state.model_release_store = FakeReleaseStore()
    client.app.state.executor_registry = ExecutorRegistry(
        dict(providers), ExecutionCertificateStore(list(certs))
    )


def _default_install(client):
    _install(
        client,
        providers={"local_cpu": _provider("local_cpu"), "local_gpu": _provider("local_gpu")},
        certs=[_cert("local_cpu"), _cert("local_gpu")],
    )


def _add_recording(client, *, recording_id="rec_x", label_space="spacenet_14"):
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=recording_id, name="0", data_path="recordings/0/raw.iq",
            data_format="complex64_le", sample_rate_hz=1e6, center_frequency_hz=0.0,
            frequency_low_hz=-5e5, frequency_high_hz=5e5, num_samples=1000, duration_s=0.001,
            dataset_name="SpaceNet", dataset_split="test", label_space=label_space,
            source_data_sha256="1" * 64,
        ))
        session.commit()


def _seed_dataset(client, *, count=3):
    with client.app.state.database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def _single(client, **extra):
    params = {"pipeline_id": PLUGIN_ID, "recording_id": "rec_x"}
    params.update(extra)
    return client.get("/api/executor-selection", params=params)


def _dataset(client, **extra):
    params = {"pipeline_id": PLUGIN_ID, "dataset_name": "SpaceNet",
              "dataset_split": "test", "dataset_label_space": "spacenet_14"}
    params.update(extra)
    return client.get("/api/executor-selection", params=params)


# 1. single-run scope shape
def test_single_recording_scope_shape(client):
    _add_recording(client)
    _default_install(client)
    response = _single(client)
    assert response.status_code == 200
    body = response.json()
    assert body["requested_mode"] == "auto"
    assert body["resolved_executor"] == "local_cpu"
    assert body["reason_code"] in _REASON_CODES
    assert body["workload_class"] in {"SMALL", "GPU_BENEFICIAL", "UNKNOWN"}
    candidates = {c["executor"]: c for c in body["candidates"]}
    assert set(candidates) == {"local_cpu", "local_gpu"}
    for candidate in candidates.values():
        for key in ("technical", "configured", "certified", "available"):
            assert isinstance(candidate[key], bool)
        assert "reason_message" in candidate


# 2. dataset scope drives GPU_BENEFICIAL
def test_dataset_scope_is_gpu_beneficial(client):
    _seed_dataset(client, count=8)
    _default_install(client)
    response = _dataset(client)
    assert response.status_code == 200
    body = response.json()
    assert body["workload_class"] == "GPU_BENEFICIAL"
    assert body["resolved_executor"] == "local_gpu"


# 3. neither scope
def test_missing_scope_is_rejected(client):
    _default_install(client)
    response = client.get("/api/executor-selection", params={"pipeline_id": PLUGIN_ID})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EXECUTION_SELECTION_REQUEST_INVALID"


# 4. both scopes
def test_both_scopes_is_rejected(client):
    _default_install(client)
    response = client.get("/api/executor-selection", params={
        "pipeline_id": PLUGIN_ID, "recording_id": "rec_x",
        "dataset_name": "SpaceNet", "dataset_split": "test",
        "dataset_label_space": "spacenet_14",
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EXECUTION_SELECTION_REQUEST_INVALID"


# 5. zero runnable
def test_zero_runnable_reports_no_executor(client):
    _add_recording(client)
    _install(client,
             providers={"local_cpu": _provider("local_cpu", available=False,
                                               reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")},
             certs=[_cert("local_cpu")])
    body = _single(client).json()
    assert body["resolved_executor"] is None
    assert body["reason_code"] == "AUTO_NO_RUNNABLE_EXECUTOR"


# 6. public boundary
def test_public_boundary_has_no_private_detail(client):
    _add_recording(client)
    _default_install(client)
    text = _single(client).text
    for forbidden in ("environment_ref", "certificate", "cgroup", "psi", "/fake/", "ssh"):
        assert forbidden not in text


# 7. technical but no provider
def test_technical_without_provider_candidate(client):
    _add_recording(client)
    _install(client, providers={"local_cpu": _provider("local_cpu")}, certs=[_cert("local_cpu")])
    candidates = {c["executor"]: c for c in _single(client).json()["candidates"]}
    local_gpu = candidates["local_gpu"]
    assert local_gpu["technical"] is True
    assert local_gpu["configured"] is False
    assert local_gpu["certified"] is False
    assert local_gpu["available"] is False


# 8. provider but no certificate
def test_provider_without_certificate_candidate(client):
    _add_recording(client)
    _install(client, providers={"local_cpu": _provider("local_cpu")}, certs=[])
    candidates = {c["executor"]: c for c in _single(client).json()["candidates"]}
    local_cpu = candidates["local_cpu"]
    assert local_cpu["configured"] is True
    assert local_cpu["certified"] is False
    assert local_cpu["available"] is False


# 9. dataset live-availability exclusion
def test_dataset_scope_excludes_live_unavailable_local_gpu(client):
    _seed_dataset(client, count=8)
    _install(client,
             providers={"local_cpu": _provider("local_cpu"),
                        "local_gpu": _provider("local_gpu", available=False,
                                               reason_code="EXECUTION_CAPABILITY_UNAVAILABLE")},
             certs=[_cert("local_cpu"), _cert("local_gpu")])
    body = _dataset(client).json()
    assert body["workload_class"] == "GPU_BENEFICIAL"
    assert body["resolved_executor"] == "local_cpu"
    candidates = {c["executor"]: c for c in body["candidates"]}
    assert candidates["local_gpu"]["available"] is False


# 10. malicious raw reason never reaches the API
def test_malicious_reason_detail_never_reaches_api(client):
    _add_recording(client)
    _install(client,
             providers={"local_cpu": _provider("local_cpu", probe=_MaliciousProbe())},
             certs=[_cert("local_cpu")])
    text = _single(client).text
    assert "C:\\secret\\runtime\\python.exe" not in text
    assert "/root/private/model.pt" not in text
    candidates = {c["executor"]: c for c in _single(client).json()["candidates"]}
    assert candidates["local_cpu"]["reason_message"] == "Executor is currently unavailable."


# 11. available candidate message is null
def test_available_candidate_reason_message_is_null(client):
    _add_recording(client)
    _default_install(client)
    candidates = {c["executor"]: c for c in _single(client).json()["candidates"]}
    local_cpu = candidates["local_cpu"]
    assert local_cpu["available"] is True
    assert local_cpu["reason_code"] is None
    assert local_cpu["reason_message"] is None


# 12. existing endpoints preserved
def test_existing_endpoints_preserved(client):
    _add_recording(client)
    _default_install(client)
    assert client.get("/api/pipelines").status_code == 200
    availability = client.get("/api/executor-availability", params={
        "recording_id": "rec_x", "pipeline_id": PLUGIN_ID, "executor": "local_cpu",
    })
    assert availability.status_code == 200
    assert availability.json()["available"] is True

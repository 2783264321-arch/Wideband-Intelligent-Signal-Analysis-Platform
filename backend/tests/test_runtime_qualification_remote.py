"""C-pre-1: remote_gpu runtime qualification / certificate-install support.

GPU REQUIRED: NO. Deterministic fakes only; no SSH, no GPU, no real inference.
The remote probe is exercised through an injected fake transport; the local GPU
doctor probe is a call-counting fake. Nothing here opens a network connection or
submits remote work.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.assets import PipelineAssetManifest
from app.remote_execution.model_release import ModelRelease, ResolvedModelRelease
from app.remote_execution.runtime import RuntimeDescriptor
from app.remote_execution.transport import RemoteRunnerExit, RemoteTransportError
from app.runtime_qualification import qualification as qual
from app.runtime_qualification.evidence import load_evidence_dir
from app.runtime_qualification.install import (
    LiveAuthority,
    install_certificate,
    load_operator_certificates,
    resolve_live_authority,
    validate_evidence_for_install,
)

_CLOCK = lambda: datetime(2026, 9, 16, tzinfo=timezone.utc)

REMOTE_GPU_PROBE_V1 = "remote_gpu_probe_v1"
REMOTE_COMMIT_V1 = "remote_commit_v1"

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
PROFILE_NAME = "autodl_primary"
LOOPBACK_PROFILE = "plan_c_loopback"
REMOTE_REF = f"remote:{PROFILE_NAME}:{COMMIT}"
LOOPBACK_REF = f"remote:{LOOPBACK_PROFILE}:{COMMIT}"
MANIFEST_SHA = "c" * 64


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeProfile:
    def __init__(
        self,
        *,
        name: str = PROFILE_NAME,
        required_remote_runtime_commit: str = COMMIT,
        device_index: int = 0,
        precision: str = "float16",
    ) -> None:
        self.name = name
        self.required_remote_runtime_commit = required_remote_runtime_commit
        self.device_type = "cuda"
        self.device_index = device_index
        self.precision = precision

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor="remote_gpu",
            device_type="cuda",
            device_index=self.device_index,
            precision=self.precision,
            environment_ref=self.name,
            environment_label=self.name,
        )


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeTransport:
    """Records every run_runner call; never touches SSH."""

    def __init__(
        self,
        *,
        payload: dict | None = None,
        raw: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._payload = payload
        self._raw = raw
        self._exc = exc

    def run_runner(self, subcommand: str, args=()):
        self.calls.append((subcommand, tuple(args)))
        if self._exc is not None:
            raise self._exc
        if self._raw is not None:
            return _Completed(self._raw)
        payload = self._payload
        if payload is None:
            payload = {
                "schema_version": 1,
                "status": "available",
                "remote_runtime_commit": COMMIT,
                "asset_manifest_sha256": MANIFEST_SHA,
                "device": 0,
            }
        return _Completed(json.dumps(payload))


class _ReleaseStore:
    def __init__(self, resolved: ResolvedModelRelease | None = None) -> None:
        self._resolved = resolved

    def resolve(self, plugin_id: str, plugin_version: str, requested: str | None) -> ResolvedModelRelease:
        if self._resolved is None:
            raise PlatformError("MODEL_RELEASE_NOT_FOUND", "no releases in this test")
        return self._resolved


class _RemoteProvider:
    name = "remote_gpu"

    def __init__(
        self,
        *,
        runtime_ref: str = REMOTE_REF,
        device_type: str = "cuda",
        precision: str = "float16",
        device_index: int = 0,
        name: str = "remote_gpu",
    ) -> None:
        self.name = name
        self._runtime_ref = runtime_ref
        self._device_type = device_type
        self._precision = precision
        self._device_index = device_index

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor=self.name,
            device_type=self._device_type,
            device_index=self._device_index,
            precision=self._precision,
            environment_ref="/remote/runtime",
            environment_label=self._runtime_ref,
        )


class _Pipelines:
    def __init__(self, definition: PipelineDefinition) -> None:
        self._definition = definition

    def get(self, plugin_id: str):
        if plugin_id != self._definition.plugin_id:
            raise PlatformError("PIPELINE_INCOMPATIBLE", "unknown plugin")

        class _Handle:
            definition = self._definition

        return _Handle()


class _ExecRegistry:
    def __init__(self, providers) -> None:
        self._providers = dict(providers)

    def providers(self):
        return dict(self._providers)


def _definition(*, model_release_required: bool = True, plugin_id: str = "remote_plugin") -> PipelineDefinition:
    return PipelineDefinition(
        id=plugin_id,
        name="Remote Plugin",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="GPU",
        cpu_supported=False,
        stages=(),
        inspectable_stages=(),
        executors_supported=("remote_gpu",),
        recommended_executor="remote_gpu",
        technical_execution_capabilities=(ExecutionCapability("remote_gpu", "cuda", "float16"),),
        model_release_required=model_release_required,
    )


def _resolved(*, release_id: str = "golden", manifest_sha: str = MANIFEST_SHA) -> ResolvedModelRelease:
    manifest = PipelineAssetManifest(
        pipeline_id="remote_plugin",
        pipeline_version="1.0",
        assets={"checkpoint": "d" * 64},
        asset_manifest_sha256=manifest_sha,
    )
    release = ModelRelease(
        plugin_id="remote_plugin",
        plugin_version="1.0",
        model_release_id=release_id,
        asset_manifest_path=Path("/nonexistent/manifest.json"),
        asset_manifest_sha256=manifest_sha,
    )
    return ResolvedModelRelease(release=release, manifest=manifest)


def _target(provider: _RemoteProvider, **overrides) -> qual.QualificationTarget:
    payload = {
        "plugin_id": "remote_plugin",
        "plugin_version": "1.0",
        "model_release_id": "golden",
        "executor": "remote_gpu",
        "runtime_ref": provider.runtime_ref,
        "runtime_descriptor": provider.runtime_descriptor().to_metadata(),
    }
    payload.update(overrides)
    return qual.QualificationTarget(**payload)


def _probe(
    provider,
    *,
    definition: PipelineDefinition | None = None,
    release_store=None,
    profile: _FakeProfile | None = None,
    transport=None,
):
    return qual.RemoteGpuTargetProbe(
        settings=Settings(runtime_family="autodl_primary"),
        definition=definition or _definition(),
        provider=provider,
        model_release_store=release_store or _ReleaseStore(_resolved()),
        profile=profile if profile is not None else _FakeProfile(),
        transport=transport if transport is not None else _FakeTransport(),
        now=_CLOCK,
    )


def _empty_repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo_certificates.json"
    path.write_text(json.dumps({"certificates": []}), encoding="utf-8")
    return path


def _authority(definition, provider, tmp_path: Path) -> LiveAuthority:
    return resolve_live_authority(
        registry=_Pipelines(definition),
        model_release_store=_ReleaseStore(_resolved()),
        executor_registry=_ExecRegistry({"remote_gpu": provider}),
        plugin_id=definition.plugin_id,
        plugin_version=definition.plugin_version,
        executor="remote_gpu",
        requested_model_release_id="golden",
        data_root=tmp_path,
    )


# ---------------------------------------------------------------------------
# R1: install eligibility is bounded to remote_gpu + remote_gpu_probe_v1
# ---------------------------------------------------------------------------
def test_r1_remote_probe_type_install_eligible_only_for_remote_gpu() -> None:
    assert qual.qualification_type_install_eligible(
        executor="remote_gpu", qualification_type=REMOTE_GPU_PROBE_V1
    ) is True
    assert qual.qualification_type_install_eligible(
        executor="local_cpu", qualification_type=REMOTE_GPU_PROBE_V1
    ) is False
    assert qual.qualification_type_install_eligible(
        executor="local_gpu", qualification_type=REMOTE_GPU_PROBE_V1
    ) is False
    assert qual.qualification_type_install_eligible(
        executor="remote_gpu", qualification_type="local_gpu_cuda_v1"
    ) is False
    assert qual.qualification_type_install_eligible(
        executor="remote_gpu", qualification_type="gpu_deferred"
    ) is False
    assert qual.qualification_type_install_eligible(
        executor="remote_gpu", qualification_type="unknown_v9"
    ) is False


# ---------------------------------------------------------------------------
# R2: select_runner(remote_gpu) is the real remote runner
# ---------------------------------------------------------------------------
def test_r2_select_runner_remote_gpu_returns_remote_runner() -> None:
    runner = qual.select_runner(executor="remote_gpu", target_probe=None)
    assert isinstance(runner, qual.RemoteGpuQualificationRunner)
    assert runner.qualification_type == REMOTE_GPU_PROBE_V1
    assert runner.identity_scheme == REMOTE_COMMIT_V1
    # A missing probe must never pass.
    results = runner.run(target=_target(_RemoteProvider()))
    assert results[0].passed is False
    assert results[0].detail == "QUALIFICATION_PROBE_UNAVAILABLE"


def test_r2_remote_runner_passes_on_success_and_bounds_errors() -> None:
    provider = _RemoteProvider()
    transport = _FakeTransport()
    probe = _probe(provider, transport=transport)
    runner = qual.RemoteGpuQualificationRunner(target_probe=probe)
    results = runner.run(target=_target(provider))
    assert results and all(r.passed for r in results)

    def _boom(target):
        raise PlatformError("QUALIFICATION_PROBE_FAILED", "remote runtime commit mismatch")

    failed = qual.RemoteGpuQualificationRunner(target_probe=_boom).run(target=_target(provider))
    assert failed[0].passed is False
    assert "remote runtime commit mismatch" in (failed[0].detail or "")

    def _crash(target):
        raise RuntimeError("raw traceback material")

    crashed = qual.RemoteGpuQualificationRunner(target_probe=_crash).run(target=_target(provider))
    assert crashed[0].passed is False
    assert crashed[0].detail == "RuntimeError"


# ---------------------------------------------------------------------------
# R3: exact production probe invocation (four identity args, probe only)
# ---------------------------------------------------------------------------
def test_r3_remote_probe_invokes_exactly_one_probe_with_four_args() -> None:
    provider = _RemoteProvider()
    transport = _FakeTransport()
    _probe(provider, transport=transport)(_target(provider))
    assert transport.calls == [
        (
            "probe",
            (
                "--plugin-id", "remote_plugin",
                "--plugin-version", "1.0",
                "--model-release-id", "golden",
                "--asset-manifest-sha256", MANIFEST_SHA,
            ),
        )
    ]
    assert [call[0] for call in transport.calls] == ["probe"]


# ---------------------------------------------------------------------------
# R4: wrong commit / manifest / device fails closed
# ---------------------------------------------------------------------------
def test_r4_wrong_response_identity_fails_closed() -> None:
    provider = _RemoteProvider()
    good = _probe(provider)
    good(_target(provider))  # happy path

    wrong_commit = _FakeTransport(payload={
        "schema_version": 1, "status": "available",
        "remote_runtime_commit": OTHER_COMMIT, "asset_manifest_sha256": MANIFEST_SHA, "device": 0,
    })
    with pytest.raises(PlatformError):
        _probe(provider, transport=wrong_commit)(_target(provider))

    wrong_manifest = _FakeTransport(payload={
        "schema_version": 1, "status": "available",
        "remote_runtime_commit": COMMIT, "asset_manifest_sha256": "e" * 64, "device": 0,
    })
    with pytest.raises(PlatformError):
        _probe(provider, transport=wrong_manifest)(_target(provider))

    wrong_device = _FakeTransport(payload={
        "schema_version": 1, "status": "available",
        "remote_runtime_commit": COMMIT, "asset_manifest_sha256": MANIFEST_SHA, "device": 1,
    })
    with pytest.raises(PlatformError):
        _probe(provider, transport=wrong_device)(_target(provider))


# ---------------------------------------------------------------------------
# R5: CLI remote qualification uses the real remote runner (not deferred)
# ---------------------------------------------------------------------------
def test_r5_cli_qualify_remote_uses_remote_probe(tmp_path: Path, capsys) -> None:
    from app.cli import CliContext, main

    definition = _definition()
    provider = _RemoteProvider()
    transport = _FakeTransport()
    repo_path = _empty_repo(tmp_path)
    context = CliContext(
        settings=Settings(runtime_family="autodl_primary", data_root=tmp_path),
        pipeline_registry=_Pipelines(definition),
        model_release_store=_ReleaseStore(_resolved()),
        executor_registry=_ExecRegistry({"remote_gpu": provider}),
        repo_certificate_path=repo_path,
        data_root=tmp_path,
    )
    context.remote_profile = _FakeProfile()
    context.remote_transport = transport

    code = main(
        ["qualify", "--plugin", "remote_plugin", "--executor", "remote_gpu", "--model-release", "golden"],
        context_factory=lambda settings: context,
    )
    assert code == 0
    evidence_dir = next((tmp_path / "qualification" / "remote_gpu").iterdir())
    evidence = load_evidence_dir(evidence_dir)
    assert evidence.passed is True
    assert evidence.qualification_type == REMOTE_GPU_PROBE_V1
    assert evidence.identity_scheme == REMOTE_COMMIT_V1
    assert evidence.runtime_ref == REMOTE_REF
    assert evidence.asset_manifest_sha256 == MANIFEST_SHA
    assert [call[0] for call in transport.calls] == ["probe"]
    assert not (tmp_path / "runtime_certificates.json").exists()


# ---------------------------------------------------------------------------
# R6: default CLI context registers remote_gpu without network I/O
# ---------------------------------------------------------------------------
def test_r6_default_context_registers_remote_provider(tmp_path: Path, monkeypatch) -> None:
    import app.remote_execution.profile as profile_module
    from app.cli import _default_context

    monkeypatch.setattr(
        profile_module.RemoteProfile, "from_env", staticmethod(lambda settings: _FakeProfile())
    )
    settings = Settings(
        runtime_family="autodl_primary",
        local_cpu_python_path=Path(sys.executable),
        data_root=tmp_path,
    )
    ctx = _default_context(settings)
    providers = ctx.executor_registry.providers()
    assert "remote_gpu" in providers
    assert providers["remote_gpu"].runtime_ref == REMOTE_REF
    assert ctx.remote_profile is not None
    assert ctx.remote_profile.name == PROFILE_NAME


# ---------------------------------------------------------------------------
# R7 / R8: exact install + cross-profile rejection
# ---------------------------------------------------------------------------
def _remote_evidence(provider, definition, *, profile=None, transport=None):
    probe = _probe(provider, definition=definition, profile=profile, transport=transport)
    target = qual.QualificationTarget(
        plugin_id=definition.plugin_id,
        plugin_version=definition.plugin_version,
        model_release_id="golden",
        executor="remote_gpu",
        runtime_ref=provider.runtime_ref,
        runtime_descriptor=provider.runtime_descriptor().to_metadata(),
    )
    return qual.run_qualification(
        target=target,
        runner=qual.RemoteGpuQualificationRunner(target_probe=probe),
        now=_CLOCK,
        asset_manifest_sha256=MANIFEST_SHA,
    )


def test_r7_matching_remote_evidence_installs_certificate(tmp_path: Path) -> None:
    definition = _definition()
    provider = _RemoteProvider()
    authority = _authority(definition, provider, tmp_path)
    evidence = _remote_evidence(provider, definition)

    certificate = validate_evidence_for_install(evidence=evidence, authority=authority)
    assert certificate.executor == "remote_gpu"
    assert certificate.device_type == "cuda"
    assert certificate.precision == "float16"
    assert certificate.runtime_ref == REMOTE_REF
    assert certificate.model_release_id == "golden"

    result = install_certificate(
        data_root=tmp_path,
        repo_certificate_path=_empty_repo(tmp_path),
        evidence=evidence,
        authority=authority,
    )
    assert result.status == "created"
    stored = load_operator_certificates(tmp_path)
    assert [c.key() for c in stored] == [certificate.key()]

    second = install_certificate(
        data_root=tmp_path,
        repo_certificate_path=_empty_repo(tmp_path),
        evidence=evidence,
        authority=authority,
    )
    assert second.status == "already_installed"


def test_r8_cross_profile_evidence_cannot_install(tmp_path: Path) -> None:
    definition = _definition()
    autodl = _RemoteProvider()
    authority = _authority(definition, autodl, tmp_path)

    loopback = _RemoteProvider(runtime_ref=LOOPBACK_REF)
    loopback_evidence = _remote_evidence(
        loopback, definition, profile=_FakeProfile(name=LOOPBACK_PROFILE)
    )
    assert loopback_evidence.passed is True
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=loopback_evidence, authority=authority)

    # runtime_ref drift and descriptor drift are rejected too.
    drifted = replace(authority, runtime_ref=f"remote:{PROFILE_NAME}:{OTHER_COMMIT}")
    matching = _remote_evidence(autodl, definition)
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=matching, authority=drifted)


# ---------------------------------------------------------------------------
# R9: remote doctor identity + never local GPU diagnostics
# ---------------------------------------------------------------------------
def test_r9_remote_spec_never_invokes_local_gpu_probe() -> None:
    from app.runtime_qualification import doctor as doctor_module

    class _Interp:
        def inspect(self, python_path):
            return doctor_module.InterpreterReport(False, None, None)

    class _Gpu:
        def __init__(self) -> None:
            self.calls = 0

        def inspect(self):
            self.calls += 1
            return doctor_module.GpuReport(True, True, "x", None, None, None, None)

    remote_gpu = _Gpu()
    report = doctor_module.build_runtime_doctor_report(
        settings=Settings(),
        provider_specs=[
            doctor_module.ProviderSpec(
                executor="remote_gpu",
                python_path=None,
                runtime_ref=REMOTE_REF,
                device_type="cuda",
                precision="float16",
                identity_scheme="remote_commit_v1",
            )
        ],
        interpreter_probe=_Interp(),
        gpu_probe=remote_gpu,
        clock=_CLOCK,
    )
    assert remote_gpu.calls == 0
    assert report.providers[0].gpu.applicable is False

    local_gpu = _Gpu()
    doctor_module.build_runtime_doctor_report(
        settings=Settings(),
        provider_specs=[
            doctor_module.ProviderSpec(
                executor="local_gpu",
                python_path=None,
                runtime_ref="local:autodl_primary:gpu:7b958347b5af",
                device_type="cuda",
                precision="float16",
                identity_scheme="bhq3_gpu_v1",
            )
        ],
        interpreter_probe=_Interp(),
        gpu_probe=local_gpu,
        clock=_CLOCK,
    )
    assert local_gpu.calls == 1


def test_r9_cli_doctor_remote_identity_uses_remote_commit_v1(tmp_path: Path, capsys) -> None:
    from app.cli import CliContext, main

    definition = _definition()
    provider = _RemoteProvider()
    context = CliContext(
        settings=Settings(runtime_family="autodl_primary", data_root=tmp_path),
        pipeline_registry=_Pipelines(definition),
        model_release_store=_ReleaseStore(_resolved()),
        executor_registry=_ExecRegistry({"remote_gpu": provider}),
        repo_certificate_path=_empty_repo(tmp_path),
        data_root=tmp_path,
    )
    context.remote_profile = _FakeProfile()

    assert main(["runtime", "doctor"], context_factory=lambda settings: context) == 0
    body = json.loads(capsys.readouterr().out)
    report = {p["executor"]: p for p in body["providers"]}["remote_gpu"]
    assert report["identity_scheme"] == REMOTE_COMMIT_V1
    assert report["identity_status"] == "match"
    assert report["gpu"]["applicable"] is False

    context.remote_profile = _FakeProfile(required_remote_runtime_commit=OTHER_COMMIT)
    assert main(["runtime", "doctor"], context_factory=lambda settings: context) == 0
    body2 = json.loads(capsys.readouterr().out)
    report2 = body2["providers"][0]
    assert report2["executor"] == "remote_gpu"
    assert report2["identity_status"] == "mismatch"


# ---------------------------------------------------------------------------
# Negative matrix (fail closed; no SSH, no GPU)
# ---------------------------------------------------------------------------
def test_negative_matrix_fail_closed() -> None:
    definition = _definition()
    provider = _RemoteProvider()
    good = _probe(provider)

    with pytest.raises(PlatformError):
        good(_target(provider, executor="local_gpu"))
    with pytest.raises(PlatformError):
        _probe(None)(_target(provider))
    with pytest.raises(PlatformError):
        _probe(_RemoteProvider(name="local_gpu"))(_target(_RemoteProvider(name="local_gpu")))
    with pytest.raises(PlatformError):
        good(_target(provider, runtime_ref=f"remote:{PROFILE_NAME}:{OTHER_COMMIT}"))
    bad_desc = provider.runtime_descriptor().to_metadata()
    bad_desc["precision"] = "float32"
    with pytest.raises(PlatformError):
        good(_target(provider, runtime_descriptor=bad_desc))
    with pytest.raises(PlatformError):
        _probe(_RemoteProvider(device_type="cpu"))(_target(_RemoteProvider(device_type="cpu")))
    with pytest.raises(PlatformError):
        good(_target(provider, plugin_version="9.9"))

    # Profile/runtime_ref mismatch: profile does not derive the target ref.
    with pytest.raises(PlatformError):
        _probe(provider, profile=_FakeProfile(name=LOOPBACK_PROFILE))(_target(provider))

    # Missing release on a release-required plugin.
    with pytest.raises(PlatformError):
        good(_target(provider, model_release_id=None))
    # Release-less remote qualification fails closed.
    with pytest.raises(PlatformError):
        _probe(provider, definition=_definition(model_release_required=False), release_store=_ReleaseStore())(
            _target(provider)
        )
    # Release mismatch.
    with pytest.raises(PlatformError):
        _probe(provider, release_store=_ReleaseStore(_resolved(release_id="other")))(_target(provider))

    # Technical capability absent.
    no_cap = replace(
        definition,
        technical_execution_capabilities=(ExecutionCapability("remote_gpu", "cuda", "float32"),),
    )
    with pytest.raises(PlatformError):
        _probe(provider, definition=no_cap)(_target(provider))

    # Transport failures.
    with pytest.raises(PlatformError):
        _probe(provider, transport=_FakeTransport(exc=RemoteTransportError("unreachable")))(_target(provider))
    with pytest.raises(PlatformError):
        _probe(provider, transport=_FakeTransport(exc=RemoteRunnerExit(3, "REMOTE_PROBE_UNAVAILABLE", "boom", "")))(
            _target(provider)
        )
    # Invalid JSON / invalid schema.
    with pytest.raises(PlatformError):
        _probe(provider, transport=_FakeTransport(raw="not-json"))(_target(provider))
    with pytest.raises(PlatformError):
        _probe(provider, transport=_FakeTransport(payload={"status": "available"}))(_target(provider))

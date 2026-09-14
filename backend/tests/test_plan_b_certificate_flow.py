"""Plan B B3: certificate / evidence flow negatives (control-plane only).

Deterministic integrity + live-authority drift tests; the real doctor/qualify/
install flow is opt-in via WSP_PLAN_B_REAL_GPU=1.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.runtime import RuntimeDescriptor
from app.runtime_qualification.evidence import (
    QualificationEvidence,
    QualificationResult,
    compute_evidence_sha256,
    evidence_payload,
    load_evidence_dir,
    write_evidence,
)
from app.runtime_qualification.install import (
    LiveAuthority,
    install_certificate,
    operator_certificate_path,
    validate_evidence_for_install,
)
from app.runtime_qualification.qualification import (
    LocalGpuQualificationRunner,
    QualificationTarget,
    run_qualification,
)

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
_CLOCK = lambda: datetime(2026, 9, 15, tzinfo=timezone.utc)

_GPU_MATERIAL = {
    "python": "3.12.3",
    "torch": "2.8.0+cu128",
    "torch_cuda": "12.8",
    "ultralytics": "8.4.114",
    "numpy": "2.3.2",
    "scipy": "1.18.0",
    "device_name": "NVIDIA GeForce RTX 5090",
    "compute_capability": "12.0",
    "driver_version": "580.105.08",
    "cuda_available": True,
}

_OLD_REF = "local:autodl_primary:gpu:aaaaaaaaaaaa"
_NEW_REF = "local:autodl_primary:gpu:bbbbbbbbbbbb"
_OLD_MANIFEST = "a" * 64
_NEW_MANIFEST = "b" * 64


def _definition(*, plugin_id: str = "dummy", release: bool = False) -> PipelineDefinition:
    return PipelineDefinition(
        id=plugin_id,
        name=plugin_id,
        version="1.0",
        label_space="spacenet_14",
        recommended_device="GPU",
        cpu_supported=False,
        stages=(),
        inspectable_stages=(),
        technical_execution_capabilities=(ExecutionCapability("local_gpu", "cuda", "float16"),),
        model_release_required=release,
    )


class _Provider:
    def __init__(self, *, runtime_ref: str, executor: str = "local_gpu",
                 device_type: str = "cuda", precision: str = "float16") -> None:
        self.name = executor
        self._runtime_ref = runtime_ref
        self._device_type = device_type
        self._precision = precision

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor=self.name,
            device_type=self._device_type,
            device_index=0 if self._device_type == "cuda" else None,
            precision=self._precision,
            environment_ref="/root/miniconda3/bin/python",
            environment_label=self._runtime_ref,
        )

    def probe(self):
        return (True, None)


def _target(provider, *, plugin_id="dummy", model_release_id=None,
            runtime_ref=None) -> QualificationTarget:
    return QualificationTarget(
        plugin_id=plugin_id,
        plugin_version="1.0",
        model_release_id=model_release_id,
        executor=provider.name,
        runtime_ref=runtime_ref or provider.runtime_ref,
        runtime_descriptor=provider.runtime_descriptor().to_metadata(),
    )


def _evidence(provider, *, plugin_id="dummy", model_release_id=None,
              asset_manifest_sha256=None, runtime_ref=None) -> QualificationEvidence:
    runner = LocalGpuQualificationRunner(target_probe=lambda target: None)
    return run_qualification(
        target=_target(provider, plugin_id=plugin_id, model_release_id=model_release_id,
                       runtime_ref=runtime_ref),
        runner=runner,
        now=_CLOCK,
        asset_manifest_sha256=asset_manifest_sha256,
    )


def _authority(provider, *, runtime_ref=None, asset_manifest_sha256=None,
               executor=None, model_release_id=None) -> LiveAuthority:
    descriptor = provider.runtime_descriptor()
    executor = executor or provider.name
    return LiveAuthority(
        definition=_definition(),
        plugin_id="dummy",
        plugin_version="1.0",
        model_release_id=model_release_id,
        asset_manifest_sha256=asset_manifest_sha256,
        executor=executor,
        runtime_ref=runtime_ref if runtime_ref is not None else provider.runtime_ref,
        runtime_descriptor=descriptor.to_metadata(),
        technical_capability_present=True,
        technical_capability=ExecutionCapability(executor, descriptor.device_type, descriptor.precision),
        provider_present=True,
    )


def test_b3_plan_resolves_expected_paths(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPTS))
    import plan_b_common as common

    assert common.PLAN_B_ROOT == Path("/root/autodl-tmp/plan_b_qual")
    assert str(common.EVIDENCE_DIR).startswith(str(common.PLAN_B_ROOT))
    assert common.ML_PYTHON == Path("/root/miniconda3/bin/python")
    assert common.GPU_RUNTIME_REF == "local:autodl_primary:gpu:7b958347b5af"


def test_integrity_tampered_evidence_rejected(tmp_path: Path) -> None:
    provider = _Provider(runtime_ref=_OLD_REF)
    evidence = _evidence(provider)
    path = write_evidence(tmp_path, evidence)
    # I1: mutate the serialized payload without recomputing evidence_sha256.
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["results"][0]["detail"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        load_evidence_dir(path.parent)
    assert exc.value.code == "QUALIFICATION_EVIDENCE_INVALID"

    # I2: vacuous evidence (empty results + passed true) is refused on write.
    vacuous = QualificationEvidence(
        schema_version=1,
        created_at="2026-09-15T00:00:00+00:00",
        plugin_id="dummy",
        plugin_version="1.0",
        model_release_id=None,
        executor="local_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref=_OLD_REF,
        runtime_descriptor=provider.runtime_descriptor().to_metadata(),
        identity_scheme="bhq3_gpu_v1",
        qualification_type="local_gpu_cuda_v1",
        results=(),
        passed=True,
        input_identity=None,
        asset_manifest_sha256=None,
        evidence_sha256="",
    )
    with pytest.raises(PlatformError) as exc2:
        write_evidence(tmp_path, vacuous)
    assert exc2.value.code == "QUALIFICATION_EVIDENCE_INVALID"


def test_authority_drift_runtime_ref_fails_closed(tmp_path: Path) -> None:
    provider = _Provider(runtime_ref=_OLD_REF)
    # Internally valid evidence for the OLD runtime_ref (SHA correctly computed).
    evidence = _evidence(provider)
    write_evidence(tmp_path, evidence)
    loaded = load_evidence_dir(
        next((tmp_path / "qualification" / "local_gpu").iterdir())
    )
    assert loaded.evidence_sha256 == compute_evidence_sha256(loaded)

    # CURRENT live authority has moved to a NEW runtime_ref.
    authority = _authority(provider, runtime_ref=_NEW_REF)
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=loaded, authority=authority)
    with pytest.raises(PlatformError):
        install_certificate(
            data_root=tmp_path,
            repo_certificate_path=tmp_path / "repo.json",
            evidence=loaded,
            authority=authority,
        )
    assert not operator_certificate_path(tmp_path).exists()


def test_authority_drift_manifest_sha_fails_closed(tmp_path: Path) -> None:
    provider = _Provider(runtime_ref=_OLD_REF)
    definition = _definition(release=True)
    runner = LocalGpuQualificationRunner(target_probe=lambda target: None)
    evidence = run_qualification(
        target=QualificationTarget(
            plugin_id="dummy",
            plugin_version="1.0",
            model_release_id="golden",
            executor="local_gpu",
            runtime_ref=_OLD_REF,
            runtime_descriptor=provider.runtime_descriptor().to_metadata(),
        ),
        runner=runner,
        now=_CLOCK,
        asset_manifest_sha256=_OLD_MANIFEST,
    )
    write_evidence(tmp_path, evidence)
    loaded = load_evidence_dir(next((tmp_path / "qualification" / "local_gpu").iterdir()))
    assert loaded.evidence_sha256 == compute_evidence_sha256(loaded)

    authority = _authority(provider, model_release_id="golden", asset_manifest_sha256=_NEW_MANIFEST)
    with pytest.raises(PlatformError):
        install_certificate(
            data_root=tmp_path,
            repo_certificate_path=tmp_path / "repo.json",
            evidence=loaded,
            authority=authority,
        )
    assert not operator_certificate_path(tmp_path).exists()


def test_negative_wrong_executor_fails_closed(tmp_path: Path) -> None:
    gpu_provider = _Provider(runtime_ref=_OLD_REF)
    evidence = _evidence(gpu_provider)
    authority = _authority(gpu_provider, executor="local_cpu")
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=evidence, authority=authority)


def test_negative_wrong_runtime_family_fails_closed() -> None:
    from app.runtime_qualification.qualification import LocalGpuTargetProbe

    provider = _Provider(runtime_ref="local:other_family:gpu:aaaaaaaaaaaa")
    probe = LocalGpuTargetProbe(
        settings=Settings(runtime_family="autodl_primary"),
        definition=_definition(),
        provider=provider,
        model_release_store=None,
        material_probe=lambda: dict(_GPU_MATERIAL),
    )
    with pytest.raises(PlatformError):
        probe(_target(provider))


def test_failed_cuda_probe_fails_closed(tmp_path: Path) -> None:
    provider = _Provider(runtime_ref=_OLD_REF)

    def _boom(target):
        raise PlatformError("QUALIFICATION_PROBE_FAILED", "cuda unavailable")

    evidence = run_qualification(
        target=_target(provider),
        runner=LocalGpuQualificationRunner(target_probe=_boom),
        now=_CLOCK,
    )
    assert evidence.passed is False
    with pytest.raises(PlatformError):
        validate_evidence_for_install(evidence=evidence, authority=_authority(provider))


def test_only_install_module_writes_operator_store() -> None:
    app_dir = REPO / "backend" / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "runtime_certificates.json" in text or "_write_operator_certificates" in text:
            offenders.append(path.relative_to(app_dir).as_posix())
    assert offenders == ["runtime_qualification/install.py"]


def test_duplicate_conflict_fails_closed(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo.json"
    repo_path.write_text(json.dumps({"certificates": []}), encoding="utf-8")
    provider = _Provider(runtime_ref=_OLD_REF)
    authority = _authority(provider)
    first = _evidence(provider)
    result = install_certificate(
        data_root=tmp_path,
        repo_certificate_path=repo_path,
        evidence=first,
        authority=authority,
    )
    assert result.status == "created"
    # Same key, different evidence content (different evidence_ref) -> conflict.
    second = run_qualification(
        target=_target(provider),
        runner=LocalGpuQualificationRunner(target_probe=lambda t: None),
        now=lambda: datetime(2026, 9, 16, tzinfo=timezone.utc),
    )
    assert second.evidence_sha256 != first.evidence_sha256
    with pytest.raises(PlatformError) as exc:
        install_certificate(
            data_root=tmp_path,
            repo_certificate_path=repo_path,
            evidence=second,
            authority=authority,
        )
    assert exc.value.code == "QUALIFICATION_EVIDENCE_INVALID"


@pytest.mark.skipif(
    os.environ.get("WSP_PLAN_B_REAL_GPU") != "1",
    reason="WSP_PLAN_B_REAL_GPU=1 not set",
)
def test_b3_real_runtime_doctor_and_qualify() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "backend") + os.pathsep + str(SCRIPTS) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "plan_b_certificate_flow.py"), "--real"],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=900,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "already_certified" in result.stdout

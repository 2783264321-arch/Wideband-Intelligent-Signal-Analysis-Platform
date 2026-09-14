"""Task 6: operator CLI (stdlib argparse).

GPU REQUIRED: NO. The production local_cpu path is exercised with the control-plane
venv as the configured (ML-free) interpreter; no GPU, no real inference.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys

import pytest

from app.cli import CliContext, main
from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.model_release import ModelRelease, ResolvedModelRelease
from app.remote_execution.runtime import RuntimeDescriptor
from app.runtime_qualification.evidence import load_evidence_dir
from app.runtime_qualification.identity import (
    LOCAL_CPU_V1,
    collect_identity_material,
    derive_generation_for_scheme,
    derive_local_runtime_ref,
)


def _definition(*, model_release_required: bool = False) -> PipelineDefinition:
    return PipelineDefinition(
        id="dummy",
        name="Dummy",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
        model_release_required=model_release_required,
    )


class _Provider:
    def __init__(self, *, name: str, runtime_ref: str, device_type: str, precision: str, probe_ok: bool = True) -> None:
        self.name = name
        self._runtime_ref = runtime_ref
        self._device_type = device_type
        self._precision = precision
        self._probe_ok = probe_ok

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor=self.name,
            device_type=self._device_type,
            device_index=0 if self._device_type == "cuda" else None,
            precision=self._precision,
            environment_ref="/ml/python",
            environment_label=self._runtime_ref,
        )

    def probe(self):
        return (self._probe_ok, None if self._probe_ok else "probe failed")


class _ReleaseStore:
    def __init__(self, resolved: ResolvedModelRelease | None = None) -> None:
        self._resolved = resolved

    def resolve(self, plugin_id: str, plugin_version: str, requested: str | None) -> ResolvedModelRelease:
        if self._resolved is None:
            raise PlatformError("MODEL_RELEASE_NOT_FOUND", "no releases configured")
        return self._resolved


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


def _control_plane_ref() -> str:
    material = collect_identity_material(Path(sys.executable), scheme=LOCAL_CPU_V1)
    generation = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=material)
    return derive_local_runtime_ref(family="autodl_primary", kind="cpu", generation=generation)


_LEGACY_CPU_REF = "local:autodl_primary:cpu:a1237f8faae7"
_SEALED_GPU_REF = "local:autodl_primary:gpu:7b958347b5af"


def _cert_dict(executor: str, runtime_ref: str, device_type: str, precision: str) -> dict:
    return {
        "plugin_id": "dummy",
        "plugin_version": "1.0",
        "model_release_id": None,
        "executor": executor,
        "device_type": device_type,
        "precision": precision,
        "runtime_ref": runtime_ref,
        "evidence_ref": "repo-default",
    }


def _context(tmp_path: Path, definition: PipelineDefinition, providers, release_store=None) -> CliContext:
    repo_path = tmp_path / "repo_certificates.json"
    repo_path.write_text(json.dumps({"certificates": []}), encoding="utf-8")
    settings = Settings(runtime_family="autodl_primary", local_cpu_python_path=Path(sys.executable), data_root=tmp_path)
    return CliContext(
        settings=settings,
        pipeline_registry=_Pipelines(definition),
        model_release_store=release_store or _ReleaseStore(),
        executor_registry=_ExecRegistry(providers),
        repo_certificate_path=repo_path,
        data_root=tmp_path,
    )


def _run(argv, context, capsys):
    return main(argv, context_factory=lambda settings: context)


def test_build_parser_surface() -> None:
    from app.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["qualify", "--plugin", "dummy", "--executor", "local_cpu"])
    assert args.model_release is None
    for argv in (
        ["runtime", "doctor"],
        ["qualify", "--plugin", "dummy", "--executor", "local_gpu"],
        ["certificate", "install", "--from", "x"],
        ["certificate", "list"],
    ):
        assert parser.parse_args(argv) is not None


def test_runtime_doctor_exits_zero(tmp_path: Path, capsys) -> None:
    provider = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": provider})
    assert _run(["runtime", "doctor"], context, capsys) == 0
    out = capsys.readouterr().out
    assert '"runtime_family": "autodl_primary"' in out or '"runtime_family":"autodl_primary"' in out


def test_qualify_local_cpu_passes_and_writes_evidence(tmp_path: Path, capsys) -> None:
    provider = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": provider})
    assert _run(["qualify", "--plugin", "dummy", "--executor", "local_cpu"], context, capsys) == 0
    evidence_dir = next((tmp_path / "qualification" / "local_cpu").iterdir())
    evidence = load_evidence_dir(evidence_dir)
    assert evidence.passed is True
    assert evidence.qualification_type == "local_cpu_smoke_v1"


def test_qualify_release_less_with_model_release_fails(tmp_path: Path, capsys) -> None:
    provider = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(model_release_required=False), {"local_cpu": provider})
    code = _run(
        ["qualify", "--plugin", "dummy", "--executor", "local_cpu", "--model-release", "golden"],
        context,
        capsys,
    )
    assert code == 1
    assert "MODEL_RELEASE_MISMATCH" in capsys.readouterr().err


def test_qualify_release_required_records_resolved_default(tmp_path: Path, capsys) -> None:
    definition = _definition(model_release_required=True)
    manifest_obj = type("M", (), {"asset_manifest_sha256": "a" * 64})()
    release = ModelRelease(
        plugin_id="dummy", plugin_version="1.0", model_release_id="golden",
        asset_manifest_path=tmp_path / "m.json", asset_manifest_sha256="a" * 64,
    )
    store = _ReleaseStore(ResolvedModelRelease(release=release, manifest=manifest_obj))
    provider = _Provider(name="remote_gpu", runtime_ref="remote:autodl_primary:deadbeef", device_type="cuda", precision="float16")
    context = _context(tmp_path, definition, {"remote_gpu": provider}, release_store=store)
    assert _run(["qualify", "--plugin", "dummy", "--executor", "remote_gpu"], context, capsys) == 1
    evidence_dir = next((tmp_path / "qualification" / "remote_gpu").iterdir())
    evidence = load_evidence_dir(evidence_dir)
    assert evidence.model_release_id == "golden"
    assert evidence.asset_manifest_sha256 == "a" * 64
    assert evidence.passed is False


def test_qualify_deferred_gpu_is_nonzero(tmp_path: Path, capsys) -> None:
    provider = _Provider(name="local_gpu", runtime_ref="local:autodl_primary:gpu:7b958347b5af", device_type="cuda", precision="float16")
    context = _context(tmp_path, _definition(), {"local_gpu": provider})
    assert _run(["qualify", "--plugin", "dummy", "--executor", "local_gpu"], context, capsys) == 1


def test_certificate_install_and_list(tmp_path: Path, capsys) -> None:
    provider = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": provider})

    assert _run(["certificate", "list"], context, capsys) == 0
    assert capsys.readouterr().out.strip() == "[]"

    assert _run(["qualify", "--plugin", "dummy", "--executor", "local_cpu"], context, capsys) == 0
    capsys.readouterr()
    evidence_dir = next((tmp_path / "qualification" / "local_cpu").iterdir())

    assert _run(["certificate", "install", "--from", str(evidence_dir)], context, capsys) == 0
    out = capsys.readouterr().out
    assert "restart" in out.lower()

    assert _run(["certificate", "list"], context, capsys) == 0
    assert "dummy" in capsys.readouterr().out


def test_certificate_install_tampered_evidence_fails(tmp_path: Path, capsys) -> None:
    provider = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": provider})
    assert _run(["qualify", "--plugin", "dummy", "--executor", "local_cpu"], context, capsys) == 0
    capsys.readouterr()
    evidence_path = next((tmp_path / "qualification" / "local_cpu").iterdir()) / "evidence.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["results"][0]["detail"] = "tampered"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    code = _run(["certificate", "install", "--from", str(evidence_path.parent)], context, capsys)
    assert code == 1
    assert "QUALIFICATION_EVIDENCE_INVALID" in capsys.readouterr().err
    assert not (tmp_path / "runtime_certificates.json").exists()


def test_unknown_command_and_missing_args_exit_nonzero(capsys) -> None:
    assert main(["bogus"], context_factory=lambda settings: None) != 0
    assert main(["qualify"], context_factory=lambda settings: None) != 0


def test_cli_constructs_real_production_probe(tmp_path: Path, capsys, monkeypatch) -> None:
    import app.cli as cli_module

    provider = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": provider})

    calls = {"count": 0}
    real_build = cli_module.build_default_target_probe

    def spy(**kwargs):
        calls["count"] += 1
        return real_build(**kwargs)

    monkeypatch.setattr(cli_module, "build_default_target_probe", spy)
    assert _run(["qualify", "--plugin", "dummy", "--executor", "local_cpu"], context, capsys) == 0
    assert calls["count"] == 1


def test_runtime_doctor_derives_and_compares_identity(tmp_path: Path, capsys) -> None:
    good = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": good})
    assert _run(["runtime", "doctor"], context, capsys) == 0
    assert '"identity_status": "match"' in capsys.readouterr().out

    stale = _Provider(name="local_cpu", runtime_ref="local:autodl_primary:cpu:ffffffffffff", device_type="cpu", precision="float32")
    context2 = _context(tmp_path, _definition(), {"local_cpu": stale})
    assert _run(["runtime", "doctor"], context2, capsys) == 0
    assert '"identity_status": "mismatch"' in capsys.readouterr().out


def test_certificate_install_corrupt_evidence_has_no_traceback(tmp_path: Path, capsys) -> None:
    provider = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": provider})
    corrupt = tmp_path / "qualification" / "local_cpu" / ("0" * 64)
    corrupt.mkdir(parents=True)
    (corrupt / "evidence.json").write_bytes(b"\xff\xfe\x00corrupt")
    code = _run(["certificate", "install", "--from", str(corrupt)], context, capsys)
    assert code == 1
    err = capsys.readouterr().err
    assert "QUALIFICATION_EVIDENCE_INVALID" in err
    assert "Traceback" not in err


def _repo_with(context, certificates) -> None:
    context.repo_certificate_path.write_text(
        json.dumps({"certificates": list(certificates)}), encoding="utf-8"
    )


def test_runtime_doctor_provenance_marks_legacy_cpu_not_gpu(tmp_path: Path, capsys) -> None:
    cpu = _Provider(name="local_cpu", runtime_ref=_LEGACY_CPU_REF, device_type="cpu", precision="float32")
    gpu = _Provider(name="local_gpu", runtime_ref=_SEALED_GPU_REF, device_type="cuda", precision="float16")
    context = _context(tmp_path, _definition(), {"local_cpu": cpu, "local_gpu": gpu})
    _repo_with(context, [
        _cert_dict("local_cpu", _LEGACY_CPU_REF, "cpu", "float32"),
        _cert_dict("local_gpu", _SEALED_GPU_REF, "cuda", "float16"),
    ])
    assert _run(["runtime", "doctor"], context, capsys) == 0
    body = json.loads(capsys.readouterr().out)
    by = {p["executor"]: p for p in body["providers"]}
    assert by["local_cpu"]["identity_scheme"] == "legacy_opaque"
    assert by["local_cpu"]["identity_status"] == "legacy_opaque"
    assert by["local_gpu"]["identity_scheme"] == "bhq3_gpu_v1"
    assert by["local_gpu"]["identity_status"] != "legacy_opaque"


def test_runtime_doctor_derivable_cpu_requires_runtime_family(tmp_path: Path, capsys) -> None:
    provider = _Provider(name="local_cpu", runtime_ref=_control_plane_ref(), device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": provider})
    context.settings = Settings(runtime_family=None, local_cpu_python_path=Path(sys.executable), data_root=tmp_path)
    assert _run(["runtime", "doctor"], context, capsys) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["providers"][0]["identity_status"] == "unavailable"


def test_runtime_doctor_family_mismatch(tmp_path: Path, capsys) -> None:
    material = collect_identity_material(Path(sys.executable), scheme=LOCAL_CPU_V1)
    generation = derive_generation_for_scheme(scheme=LOCAL_CPU_V1, material=material)
    other_ref = derive_local_runtime_ref(family="other_family", kind="cpu", generation=generation)
    provider = _Provider(name="local_cpu", runtime_ref=other_ref, device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": provider})
    assert _run(["runtime", "doctor"], context, capsys) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["providers"][0]["identity_status"] == "mismatch"


def test_runtime_doctor_local_gpu_never_legacy_and_family_authority(tmp_path: Path, capsys) -> None:
    gpu = _Provider(name="local_gpu", runtime_ref=_SEALED_GPU_REF, device_type="cuda", precision="float16")

    # repo-default local_gpu + runtime_family=None -> bhq3_gpu_v1 / unavailable / NOT legacy.
    context = _context(tmp_path, _definition(), {"local_gpu": gpu})
    context.settings = Settings(runtime_family=None, data_root=tmp_path)
    _repo_with(context, [_cert_dict("local_gpu", _SEALED_GPU_REF, "cuda", "float16")])
    assert _run(["runtime", "doctor"], context, capsys) == 0
    report = json.loads(capsys.readouterr().out)["providers"][0]
    assert report["identity_scheme"] == "bhq3_gpu_v1"
    assert report["identity_status"] == "unavailable"
    assert report["identity_status"] != "legacy_opaque"

    # family mismatch -> bhq3_gpu_v1 / mismatch.
    mism = _Provider(name="local_gpu", runtime_ref="local:other_family:gpu:abcdefabcdef", device_type="cuda", precision="float16")
    context2 = _context(tmp_path, _definition(), {"local_gpu": mism})
    _repo_with(context2, [])
    assert _run(["runtime", "doctor"], context2, capsys) == 0
    report2 = json.loads(capsys.readouterr().out)["providers"][0]
    assert report2["identity_scheme"] == "bhq3_gpu_v1"
    assert report2["identity_status"] == "mismatch"

    # kind=cpu -> bhq3_gpu_v1 / mismatch.
    wrong_kind = _Provider(name="local_gpu", runtime_ref="local:autodl_primary:cpu:abcdefabcdef", device_type="cuda", precision="float16")
    context3 = _context(tmp_path, _definition(), {"local_gpu": wrong_kind})
    _repo_with(context3, [])
    assert _run(["runtime", "doctor"], context3, capsys) == 0
    report3 = json.loads(capsys.readouterr().out)["providers"][0]
    assert report3["identity_scheme"] == "bhq3_gpu_v1"
    assert report3["identity_status"] == "mismatch"


def test_runtime_doctor_legacy_cpu_without_family(tmp_path: Path, capsys) -> None:
    cpu = _Provider(name="local_cpu", runtime_ref=_LEGACY_CPU_REF, device_type="cpu", precision="float32")
    context = _context(tmp_path, _definition(), {"local_cpu": cpu})
    context.settings = Settings(runtime_family=None, data_root=tmp_path)
    _repo_with(context, [_cert_dict("local_cpu", _LEGACY_CPU_REF, "cpu", "float32")])
    assert _run(["runtime", "doctor"], context, capsys) == 0
    report = json.loads(capsys.readouterr().out)["providers"][0]
    assert report["identity_scheme"] == "legacy_opaque"
    assert report["identity_status"] == "legacy_opaque"


# ---------------------------------------------------------------------------
# B1: local_gpu qualification CLI dispatch (deterministic; no GPU)
# ---------------------------------------------------------------------------

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


def _gpu_definition() -> PipelineDefinition:
    return PipelineDefinition(
        id="dummy",
        name="Dummy",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="GPU",
        cpu_supported=False,
        stages=(),
        inspectable_stages=(),
        technical_execution_capabilities=(ExecutionCapability("local_gpu", "cuda", "float16"),),
        model_release_required=False,
    )


def test_qualify_local_gpu_builds_real_probe_and_writes_evidence(tmp_path: Path, capsys, monkeypatch) -> None:
    import app.runtime_qualification.qualification as qual
    from app.runtime_qualification.identity import (
        BHQ3_GPU_V1,
        derive_generation_for_scheme,
        derive_local_runtime_ref,
    )

    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=_GPU_MATERIAL)
    gpu_ref = derive_local_runtime_ref(family="autodl_primary", kind="gpu", generation=generation)
    assert gpu_ref == "local:autodl_primary:gpu:7b958347b5af"

    monkeypatch.setattr(
        qual, "collect_identity_material",
        lambda path, *, scheme, runner=None: dict(_GPU_MATERIAL),
    )
    provider = _Provider(name="local_gpu", runtime_ref=gpu_ref, device_type="cuda", precision="float16")
    context = _context(tmp_path, _gpu_definition(), {"local_gpu": provider})
    assert _run(["qualify", "--plugin", "dummy", "--executor", "local_gpu"], context, capsys) == 0
    evidence_dir = next((tmp_path / "qualification" / "local_gpu").iterdir())
    evidence = load_evidence_dir(evidence_dir)
    assert evidence.passed is True
    assert evidence.qualification_type == "local_gpu_cuda_v1"
    assert evidence.identity_scheme == "bhq3_gpu_v1"
    assert evidence.runtime_ref == gpu_ref


def test_qualify_local_gpu_missing_capability_is_nonzero_not_deferred(tmp_path: Path, capsys, monkeypatch) -> None:
    import app.runtime_qualification.qualification as qual
    from app.runtime_qualification.identity import (
        BHQ3_GPU_V1,
        derive_generation_for_scheme,
        derive_local_runtime_ref,
    )

    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=_GPU_MATERIAL)
    gpu_ref = derive_local_runtime_ref(family="autodl_primary", kind="gpu", generation=generation)
    monkeypatch.setattr(
        qual, "collect_identity_material",
        lambda path, *, scheme, runner=None: dict(_GPU_MATERIAL),
    )
    provider = _Provider(name="local_gpu", runtime_ref=gpu_ref, device_type="cuda", precision="float16")
    # _definition() declares only a local_cpu capability.
    context = _context(tmp_path, _definition(), {"local_gpu": provider})
    assert _run(["qualify", "--plugin", "dummy", "--executor", "local_gpu"], context, capsys) == 1
    evidence_dir = next((tmp_path / "qualification" / "local_gpu").iterdir())
    evidence = load_evidence_dir(evidence_dir)
    assert evidence.passed is False
    assert evidence.qualification_type == "local_gpu_cuda_v1"


def test_cli_builds_real_local_gpu_probe(tmp_path: Path, capsys, monkeypatch) -> None:
    import app.cli as cli_module
    import app.runtime_qualification.qualification as qual
    from app.runtime_qualification.identity import (
        BHQ3_GPU_V1,
        derive_generation_for_scheme,
        derive_local_runtime_ref,
    )

    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=_GPU_MATERIAL)
    gpu_ref = derive_local_runtime_ref(family="autodl_primary", kind="gpu", generation=generation)
    monkeypatch.setattr(
        qual, "collect_identity_material",
        lambda path, *, scheme, runner=None: dict(_GPU_MATERIAL),
    )
    provider = _Provider(name="local_gpu", runtime_ref=gpu_ref, device_type="cuda", precision="float16")
    context = _context(tmp_path, _gpu_definition(), {"local_gpu": provider})

    calls = {"count": 0}
    real_build = cli_module.build_default_target_probe

    def spy(**kwargs):
        calls["count"] += 1
        return real_build(**kwargs)

    monkeypatch.setattr(cli_module, "build_default_target_probe", spy)
    assert _run(["qualify", "--plugin", "dummy", "--executor", "local_gpu"], context, capsys) == 0
    assert calls["count"] == 1

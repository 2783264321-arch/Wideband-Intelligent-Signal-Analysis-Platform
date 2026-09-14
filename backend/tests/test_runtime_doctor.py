"""Task 1: portable runtime doctor report + runtime_family setting.

GPU REQUIRED: NO. All probes are injected fakes; no GPU, no CUDA, no real
inference, no cgroup//proc dependency.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.runtime_qualification.doctor import (
    GpuReport,
    InterpreterReport,
    ProviderSpec,
    build_runtime_doctor_report,
)

_FIXED_CLOCK = lambda: datetime(2026, 9, 14, tzinfo=timezone.utc)


class _FakeInterpreterProbe:
    def __init__(self, report: InterpreterReport) -> None:
        self._report = report
        self.calls: list[Path | None] = []

    def inspect(self, python_path: Path | None) -> InterpreterReport:
        self.calls.append(python_path)
        return self._report


class _FakeGpuProbe:
    def __init__(self, report: GpuReport) -> None:
        self._report = report
        self.calls = 0

    def inspect(self) -> GpuReport:
        self.calls += 1
        return self._report


def _cpu_gpu_report() -> GpuReport:
    return GpuReport(
        applicable=False,
        available=False,
        device_name=None,
        compute_capability=None,
        cuda_runtime=None,
        driver_version=None,
        reason="cpu-only runtime; GPU diagnostics not applicable",
    )


def _gpu_gpu_report() -> GpuReport:
    return GpuReport(
        applicable=True,
        available=True,
        device_name="NVIDIA GeForce RTX 5090",
        compute_capability="12.0",
        cuda_runtime="12.8",
        driver_version="580.105.08",
        reason=None,
    )


def test_cpu_only_report_is_gpu_free_and_portable() -> None:
    interp = _FakeInterpreterProbe(InterpreterReport(True, "3.12.3", "/ml/python"))
    gpu = _FakeGpuProbe(_cpu_gpu_report())
    specs = [
        ProviderSpec(
            executor="local_cpu",
            python_path=Path("/ml/python"),
            runtime_ref="local:autodl_primary:cpu:abc123def456",
            device_type="cpu",
            precision="float32",
            identity_scheme="local_cpu_v1",
        )
    ]

    report = build_runtime_doctor_report(
        settings=Settings(runtime_family="autodl_primary"),
        provider_specs=specs,
        interpreter_probe=interp,
        gpu_probe=gpu,
        clock=_FIXED_CLOCK,
        host_platform="Linux",
    )

    assert report.runtime_family == "autodl_primary"
    assert report.control_plane_python == sys.executable
    assert report.platform_system == "Linux"
    assert isinstance(report.architecture, str) and report.architecture
    assert report.schema_version == 1
    assert report.created_at == "2026-09-14T00:00:00+00:00"

    provider = report.providers[0]
    assert provider.executor == "local_cpu"
    assert provider.device_type == "cpu"
    assert provider.gpu.applicable is False
    assert provider.configured is True
    # A pure local_cpu report must never touch GPU diagnostics.
    assert gpu.calls == 0


def test_configured_gpu_provider_surfaces_scheme_and_status() -> None:
    interp = _FakeInterpreterProbe(InterpreterReport(True, "3.12.3", "/ml/python"))
    gpu = _FakeGpuProbe(_gpu_gpu_report())

    def resolver(spec, settings):
        return (spec.runtime_ref, spec.runtime_ref, "bhq3_gpu_v1", "match")

    specs = [
        ProviderSpec(
            executor="local_gpu",
            python_path=Path("/ml/python"),
            runtime_ref="local:autodl_primary:gpu:7b958347b5af",
            device_type="cuda",
            precision="float16",
            identity_scheme="bhq3_gpu_v1",
        )
    ]

    report = build_runtime_doctor_report(
        settings=Settings(runtime_family="autodl_primary"),
        provider_specs=specs,
        interpreter_probe=interp,
        gpu_probe=gpu,
        identity_resolver=resolver,
        clock=_FIXED_CLOCK,
        host_platform="Linux",
    )

    provider = report.providers[0]
    assert provider.identity_scheme == "bhq3_gpu_v1"
    assert provider.identity_status == "match"
    assert provider.runtime_ref == "local:autodl_primary:gpu:7b958347b5af"
    assert gpu.calls == 1


def test_identity_status_values_surface_verbatim() -> None:
    interp = _FakeInterpreterProbe(InterpreterReport(False, None, None))
    gpu = _FakeGpuProbe(_cpu_gpu_report())

    statuses = ("match", "mismatch", "legacy_opaque", "not_configured", "unavailable")
    for status in statuses:
        specs = [
            ProviderSpec(
                executor="local_cpu",
                python_path=None,
                runtime_ref="local:autodl_primary:cpu:abc123def456",
                device_type="cpu",
                precision="float32",
                identity_scheme="local_cpu_v1",
            )
        ]
        report = build_runtime_doctor_report(
            settings=Settings(),
            provider_specs=specs,
            interpreter_probe=interp,
            gpu_probe=gpu,
            identity_resolver=lambda spec, settings, s=status: (
                spec.runtime_ref,
                None,
                spec.identity_scheme,
                s,
            ),
            clock=_FIXED_CLOCK,
            host_platform="Windows",
        )
        assert report.providers[0].identity_status == status


def test_unconfigured_provider_reports_not_configured() -> None:
    interp = _FakeInterpreterProbe(InterpreterReport(False, None, None))
    gpu = _FakeGpuProbe(_cpu_gpu_report())
    specs = [
        ProviderSpec(
            executor="local_cpu",
            python_path=None,
            runtime_ref=None,
            device_type="cpu",
            precision="float32",
            identity_scheme="unavailable",
        )
    ]

    report = build_runtime_doctor_report(
        settings=Settings(),
        provider_specs=specs,
        interpreter_probe=interp,
        gpu_probe=gpu,
        clock=_FIXED_CLOCK,
        host_platform="Windows",
    )

    assert report.runtime_family is None
    assert report.providers[0].configured is False
    assert report.providers[0].identity_status == "not_configured"


def test_doctor_module_has_no_cgroup_or_proc_dependency() -> None:
    import app.runtime_qualification.doctor as doctor

    source = Path(doctor.__file__).read_text(encoding="utf-8")
    assert "cgroup" not in source
    assert "/proc" not in source


def test_operator_json_includes_private_executable_id() -> None:
    interp = _FakeInterpreterProbe(InterpreterReport(True, "3.12.3", "/ml/python"))
    gpu = _FakeGpuProbe(_cpu_gpu_report())
    specs = [
        ProviderSpec(
            executor="local_cpu",
            python_path=Path("/ml/python"),
            runtime_ref="local:autodl_primary:cpu:abc123def456",
            device_type="cpu",
            precision="float32",
            identity_scheme="local_cpu_v1",
        )
    ]
    report = build_runtime_doctor_report(
        settings=Settings(),
        provider_specs=specs,
        interpreter_probe=interp,
        gpu_probe=gpu,
        clock=_FIXED_CLOCK,
        host_platform="Linux",
    )
    payload = report.to_operator_json()
    assert payload["providers"][0]["interpreter"]["executable_id"] == "/ml/python"
    assert payload["runtime_family"] is None


def test_runtime_family_setting_validation() -> None:
    assert Settings(runtime_family="autodl_primary").runtime_family == "autodl_primary"
    assert Settings(runtime_family=None).runtime_family is None
    for unsafe in ("../escape", "has space", "a/b", "..", ".hidden", "", "x" * 256):
        with pytest.raises(ValidationError):
            Settings(runtime_family=unsafe)

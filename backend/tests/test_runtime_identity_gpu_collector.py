"""Plan B B0: scheme-aware GPU runtime identity collection (fail-closed).

GPU REQUIRED: NO for the deterministic unit tests below. The opt-in integration
test runs against the real ML interpreter when CUDA is present.

The control plane never imports torch/ultralytics: material is collected inside
the configured ML interpreter through a fixed platform-owned probe.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.errors import PlatformError
from app.runtime_qualification.identity import (
    BHQ3_GPU_V1,
    BHQ3_GPU_V1_FIELDS,
    LOCAL_CPU_V1,
    LOCAL_CPU_V1_FIELDS,
    assemble_bhq3_gpu_material,
    collect_bhq3_gpu_identity_material,
    collect_identity_material,
    derive_generation_for_scheme,
)

# The exact recorded BHQ-3 V1 sealed material.
BHQ3_MATERIAL = {
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

# Raw torch probe material: the canonical 10 fields except driver_version is
# replaced by the internal device_count validation field.
PROBE_MATERIAL = {
    "python": "3.12.3",
    "torch": "2.8.0+cu128",
    "torch_cuda": "12.8",
    "ultralytics": "8.4.114",
    "numpy": "2.3.2",
    "scipy": "1.18.0",
    "device_name": "NVIDIA GeForce RTX 5090",
    "compute_capability": "12.0",
    "cuda_available": True,
    "device_count": 1,
}

CPU_MATERIAL = {
    "python": "3.12.3",
    "platform_system": "Linux",
    "architecture": "x86_64",
    "torch": "2.8.0+cu128",
    "ultralytics": "8.4.114",
    "numpy": "2.3.2",
    "scipy": "1.18.0",
}

_REAL_ML_PYTHON = Path("/root/miniconda3/bin/python")


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def _fake_runner(*, probe_stdout="", probe_rc=0, driver_stdout="", driver_rc=0,
                 calls=None):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append(list(argv))
        if argv and argv[0] == "nvidia-smi":
            return _completed(driver_stdout, driver_rc)
        return _completed(probe_stdout, probe_rc)

    return run


def _probe_payload(material) -> str:
    return json.dumps({"ok": True, "reason": None, "material": material})


def test_bhq3_gpu_collector_reproduces_recorded_identity() -> None:
    calls: list[list[str]] = []
    runner = _fake_runner(
        probe_stdout=_probe_payload(PROBE_MATERIAL),
        driver_stdout="580.105.08\n",
        calls=calls,
    )
    material = collect_identity_material(
        Path(sys.executable), scheme=BHQ3_GPU_V1, runner=runner
    )
    assert material == BHQ3_MATERIAL
    assert set(material) == set(BHQ3_GPU_V1_FIELDS)
    assert "device_count" not in material
    assert derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=material) == (
        "7b958347b5af"
    )
    # The only external command is the fixed driver query, run with shell=False.
    driver_calls = [c for c in calls if c and c[0] == "nvidia-smi"]
    assert driver_calls == [
        ["nvidia-smi", "-i", "0", "--query-gpu=driver_version", "--format=csv,noheader"]
    ]


def test_dispatch_never_feeds_cpu_material_to_gpu_scheme() -> None:
    runner = _fake_runner(
        probe_stdout=_probe_payload(CPU_MATERIAL), driver_stdout="580.105.08\n"
    )
    with pytest.raises(PlatformError) as exc:
        collect_identity_material(Path(sys.executable), scheme=BHQ3_GPU_V1, runner=runner)
    assert exc.value.code == "RUNTIME_IDENTITY_INVALID"


def test_dispatch_never_feeds_gpu_material_to_cpu_scheme() -> None:
    runner = _fake_runner(probe_stdout=_probe_payload(PROBE_MATERIAL))
    with pytest.raises(PlatformError) as exc:
        collect_identity_material(Path(sys.executable), scheme=LOCAL_CPU_V1, runner=runner)
    assert exc.value.code == "RUNTIME_IDENTITY_INVALID"


def test_unknown_scheme_rejected() -> None:
    with pytest.raises(PlatformError) as exc:
        collect_identity_material(Path(sys.executable), scheme="not_a_scheme")
    assert exc.value.code == "RUNTIME_IDENTITY_INVALID"


def test_assemble_bhq3_gpu_material_fails_closed() -> None:
    def _raises(material, driver="580.105.08"):
        with pytest.raises(PlatformError) as exc:
            assemble_bhq3_gpu_material(material, driver)
        assert exc.value.code == "RUNTIME_IDENTITY_UNAVAILABLE"

    _raises({**PROBE_MATERIAL, "cuda_available": False})
    _raises({**PROBE_MATERIAL, "device_count": 0})
    _raises({**PROBE_MATERIAL, "device_name": ""})
    _raises({k: v for k, v in PROBE_MATERIAL.items() if k != "device_name"})
    _raises({**PROBE_MATERIAL, "compute_capability": ""})
    _raises({k: v for k, v in PROBE_MATERIAL.items() if k != "compute_capability"})
    _raises({**PROBE_MATERIAL, "torch": None})
    _raises({k: v for k, v in PROBE_MATERIAL.items() if k != "scipy"})
    _raises(PROBE_MATERIAL, driver="")
    _raises(PROBE_MATERIAL, driver="   ")
    _raises(PROBE_MATERIAL, driver="a\nb")
    _raises(PROBE_MATERIAL, driver="unknown")


def test_gpu_collector_fails_closed_on_probe_failure() -> None:
    def _raises(runner):
        with pytest.raises(PlatformError) as exc:
            collect_bhq3_gpu_identity_material(Path(sys.executable), runner=runner)
        assert exc.value.code == "RUNTIME_IDENTITY_UNAVAILABLE"

    _raises(_fake_runner(probe_rc=1))
    _raises(_fake_runner(probe_stdout=json.dumps({"ok": False, "reason": "cuda unavailable"})))
    _raises(_fake_runner(probe_stdout="not json"))
    with pytest.raises(PlatformError) as exc:
        collect_bhq3_gpu_identity_material(None)
    assert exc.value.code == "RUNTIME_IDENTITY_UNAVAILABLE"
    with pytest.raises(PlatformError) as exc2:
        collect_bhq3_gpu_identity_material(Path("/does/not/exist/python"))
    assert exc2.value.code == "RUNTIME_IDENTITY_UNAVAILABLE"


def test_gpu_collector_fails_closed_on_driver_failure() -> None:
    def _raises(driver_stdout, driver_rc=0):
        runner = _fake_runner(
            probe_stdout=_probe_payload(PROBE_MATERIAL),
            driver_stdout=driver_stdout,
            driver_rc=driver_rc,
        )
        with pytest.raises(PlatformError) as exc:
            collect_bhq3_gpu_identity_material(Path(sys.executable), runner=runner)
        assert exc.value.code == "RUNTIME_IDENTITY_UNAVAILABLE"

    _raises("", 1)
    _raises("")
    _raises("   \n")
    _raises("580.105.08\n123.45.67\n")
    _raises("unknown")


def test_gpu_probe_script_is_platform_owned_and_bounded() -> None:
    from app.runtime_qualification import identity as identity_module

    script = identity_module._BHQ3_GPU_TORCH_PROBE_SCRIPT
    for token in ("torch", "ultralytics", "numpy", "scipy"):
        assert token in script
    assert "subprocess" not in script
    assert 'shell=True' not in script
    assert '"unknown"' not in script
    query = identity_module._BHQ3_GPU_DRIVER_QUERY
    assert query == (
        "nvidia-smi", "-i", "0",
        "--query-gpu=driver_version", "--format=csv,noheader",
    )


@pytest.mark.skipif(
    __import__("os").environ.get("WSP_PLAN_B_GPU_IDENTITY") != "1",
    reason="WSP_PLAN_B_GPU_IDENTITY=1 not set",
)
def test_integration_bhq3_gpu_collector_real_interpreter() -> None:
    if not _REAL_ML_PYTHON.is_file():
        pytest.skip("ML interpreter unavailable")
    material = collect_bhq3_gpu_identity_material(_REAL_ML_PYTHON)
    assert material == BHQ3_MATERIAL
    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=material)
    assert generation == "7b958347b5af"

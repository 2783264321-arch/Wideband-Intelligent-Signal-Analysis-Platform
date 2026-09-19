"""Runtime discovery: which local interpreters can actually run pytorch.

Discovery is diagnostic only and must be safe (no heavy imports, no crashing on
missing interpreters, no rewriting of configuration).
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from app.core.config import Settings
from app.runtime_qualification import doctor


class _Completed:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _fake_probe(*, torch: bool = False, ultralytics: bool = False, version: str = "3.10.20", fail: bool = False):
    def run(command, **kwargs):  # noqa: ANN001 - test double
        if fail:
            return _Completed("", returncode=1)
        payload = (
            '{"python": "%s", "torch": %s, "ultralytics": %s}'
            % (version, "true" if torch else "false", "true" if ultralytics else "false")
        )
        return _Completed(payload)

    return run


def _python_file(env_dir: Path) -> Path:
    env_dir.mkdir(parents=True, exist_ok=True)
    python = env_dir / ("python.exe" if os.name == "nt" else "python")
    python.write_text("", encoding="utf-8")
    return python


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        label_space_root=tmp_path / "labels",
        database_url=f"sqlite:///{tmp_path / 't.db'}",
        **overrides,
    )


def _clear_conda_env(monkeypatch) -> None:
    for name in ("CONDA_ENVS_PATH", "CONDA_PREFIX", "CONDA_EXE", "WSP_EXTRA_PYTHON_PATHS"):
        monkeypatch.delenv(name, raising=False)


def test_reports_configured_local_cpu_interpreter(tmp_path, monkeypatch):
    _clear_conda_env(monkeypatch)
    python = _python_file(tmp_path / "envs" / "pytorch")
    monkeypatch.setattr(doctor.subprocess, "run", _fake_probe(torch=True, ultralytics=True))

    found = doctor.discover_local_runtimes(_settings(tmp_path, local_cpu_python_path=python))

    item = next(r for r in found if Path(r.python_path) == python)
    assert item.torch is True
    assert item.ultralytics is True
    assert item.version == "3.10.20"
    assert item.is_configured_local_cpu is True
    assert item.is_control_plane is False


def test_flags_the_control_plane_interpreter(tmp_path, monkeypatch):
    _clear_conda_env(monkeypatch)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_probe(torch=False))

    found = doctor.discover_local_runtimes(_settings(tmp_path))

    item = next(r for r in found if r.is_control_plane)
    assert Path(item.python_path) == Path(sys.executable)
    assert item.torch is False


def test_discovers_conda_environments_from_env_var(tmp_path, monkeypatch):
    _clear_conda_env(monkeypatch)
    envs_root = tmp_path / "miniconda3" / "envs"
    python = _python_file(envs_root / "pytorch")
    monkeypatch.setenv("CONDA_ENVS_PATH", str(envs_root))
    monkeypatch.setattr(doctor.subprocess, "run", _fake_probe(torch=True, ultralytics=True))

    found = doctor.discover_local_runtimes(_settings(tmp_path))

    item = next(r for r in found if Path(r.python_path) == python)
    assert item.torch is True
    assert item.ultralytics is True
    assert item.is_configured_local_cpu is False


def test_discovers_operator_declared_extra_paths(tmp_path, monkeypatch):
    _clear_conda_env(monkeypatch)
    python = _python_file(tmp_path / "somewhere" / "py")
    monkeypatch.setenv("WSP_EXTRA_PYTHON_PATHS", str(python) + os.pathsep + str(tmp_path / "missing" / "python"))
    monkeypatch.setattr(doctor.subprocess, "run", _fake_probe(torch=True))

    found = doctor.discover_local_runtimes(_settings(tmp_path))

    assert any(Path(r.python_path) == python and r.torch for r in found)


def test_missing_configured_interpreter_is_skipped(tmp_path, monkeypatch):
    _clear_conda_env(monkeypatch)
    missing = tmp_path / "nope" / ("python.exe" if os.name == "nt" else "python")
    monkeypatch.setattr(doctor.subprocess, "run", _fake_probe(torch=True))

    found = doctor.discover_local_runtimes(_settings(tmp_path, local_cpu_python_path=missing))

    assert all(Path(r.python_path) != missing for r in found)


def test_unavailable_probe_does_not_crash(tmp_path, monkeypatch):
    _clear_conda_env(monkeypatch)
    python = _python_file(tmp_path / "envs" / "broken")
    monkeypatch.setenv("WSP_EXTRA_PYTHON_PATHS", str(python))
    monkeypatch.setattr(doctor.subprocess, "run", _fake_probe(fail=True))

    # The control-plane entry (a real file, but its probe fails here) is dropped;
    # no exception escapes.
    found = doctor.discover_local_runtimes(_settings(tmp_path))
    assert isinstance(found, list)


def test_derived_runtime_ref_round_trips_and_fails_closed():
    """runtime_ref is a pure function of interpreter material, so an operator
    cannot invent one; a wrong family must fail closed."""
    import pytest

    from app.core.errors import PlatformError
    from app.runtime_qualification import identity

    material = {
        "python": "3.10.20",
        "platform_system": "Windows",
        "architecture": "AMD64",
        "torch": "2.12.0+cpu",
        "ultralytics": "8.4.155",
        "numpy": "2.2.6",
        "scipy": "1.15.3",
    }
    generation = identity.derive_generation_for_scheme(
        scheme=identity.LOCAL_CPU_V1, material=material
    )
    runtime_ref = identity.derive_local_runtime_ref(
        family="pytorch", kind="cpu", generation=generation
    )
    assert runtime_ref.startswith("local:pytorch:cpu:")
    assert len(runtime_ref.rsplit(":", 1)[-1]) == 12

    identity.validate_runtime_ref_against_material(
        runtime_ref=runtime_ref,
        family="pytorch",
        kind="cpu",
        scheme=identity.LOCAL_CPU_V1,
        material=material,
    )

    with pytest.raises(PlatformError):
        identity.validate_runtime_ref_against_material(
            runtime_ref=runtime_ref,
            family="some-other-family",
            kind="cpu",
            scheme=identity.LOCAL_CPU_V1,
            material=material,
        )

"""TASK D2 — local inference-worker providers use a configured ML interpreter."""

import sys
from pathlib import Path

import pytest

from app.analysis.local_executor import LocalInferenceWorkerProvider, build_local_providers
from app.core.config import Settings


def _settings(tmp_path: Path, **overrides) -> Settings:
    fields = dict(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        label_space_root=tmp_path / "label_spaces",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )
    fields.update(overrides)
    return Settings(**fields)


def _fake_interpreter(tmp_path: Path) -> Path:
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    return interpreter


def _provider(tmp_path: Path, *, executor_kind: str = "local_cpu") -> LocalInferenceWorkerProvider:
    return LocalInferenceWorkerProvider(
        interpreter=_fake_interpreter(tmp_path),
        runtime_ref="local-gen-1",
        work_root=tmp_path,
        executor_kind=executor_kind,
    )


def test_local_cpu_descriptor_public_private(tmp_path):
    descriptor = _provider(tmp_path, executor_kind="local_cpu").runtime_descriptor()
    assert descriptor.executor == "local_cpu"
    assert descriptor.device_type == "cpu"
    assert descriptor.device_index is None
    assert descriptor.precision == "float32"
    assert descriptor.environment_ref == str(tmp_path / "python")  # private
    assert descriptor.environment_label == "local-gen-1"  # public logical label
    assert descriptor.public_projection() == {
        "executor": "local_cpu",
        "device": "cpu",
        "environment": "local-gen-1",
    }


def test_local_gpu_descriptor(tmp_path):
    descriptor = _provider(tmp_path, executor_kind="local_gpu").runtime_descriptor()
    assert (descriptor.executor, descriptor.device_type, descriptor.device_index, descriptor.precision) == (
        "local_gpu",
        "cuda",
        0,
        "float16",
    )


def test_launch_uses_configured_interpreter_not_control_plane(tmp_path, monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 4242

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        captured["shell"] = kwargs.get("shell")
        return FakeProcess()

    monkeypatch.setattr("app.analysis.local_executor.subprocess.Popen", fake_popen)

    provider = _provider(tmp_path, executor_kind="local_cpu")
    pid = provider.launch("run_local", coordinator_token=None)

    assert pid == 4242
    assert captured["argv"][0] == str(tmp_path / "python")
    assert captured["argv"][0] != sys.executable
    assert captured["argv"][1:3] == ["-m", "app.analysis.local_inference_worker"]
    assert captured["shell"] is False


def test_unset_interpreter_or_runtime_ref_not_registered(tmp_path):
    assert build_local_providers(_settings(tmp_path)) == {}
    interpreter = _fake_interpreter(tmp_path)

    # interpreter set but runtime_ref unset -> not registered
    assert build_local_providers(
        _settings(tmp_path, local_cpu_python_path=interpreter)
    ) == {}
    # runtime_ref set but interpreter unset -> not registered
    assert build_local_providers(
        _settings(tmp_path, local_cpu_runtime_ref="local-gen-1")
    ) == {}


def test_configured_local_cpu_provider_registered(tmp_path):
    interpreter = _fake_interpreter(tmp_path)
    providers = build_local_providers(
        _settings(
            tmp_path,
            local_cpu_python_path=interpreter,
            local_cpu_runtime_ref="local-gen-1",
        )
    )
    assert set(providers) == {"local_cpu"}
    assert providers["local_cpu"].runtime_ref == "local-gen-1"


def test_probe_reports_missing_interpreter(tmp_path):
    provider = LocalInferenceWorkerProvider(
        interpreter=tmp_path / "missing_python",
        runtime_ref="local-gen-1",
        work_root=tmp_path,
        executor_kind="local_cpu",
    )
    ok, reason = provider.probe()
    assert ok is False
    assert reason

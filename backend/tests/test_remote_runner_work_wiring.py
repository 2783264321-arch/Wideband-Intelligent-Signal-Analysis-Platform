"""Runner ``work`` lazy wiring tests.

``_cli_work`` must lazily build ``RemoteWorkerContext`` from env, load the
frozen batch from ``job_root/request.json``, construct the production generic
``PluginItemExecutor`` (via ``_build_work_executor``), and delegate to
``run_work`` (which remains the lifecycle/write-once/status owner). Importing
runner stays GPU-library-free. No GPU.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.remote_execution import runner as runner_module
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.package_publisher import publish_package
from app.remote_execution.plugin_executor import PluginItemExecutor
from app.remote_execution.runner import create_or_attach
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)
from app.remote_execution.worker_context import RemoteWorkerContext

ORCHESTRATOR_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
RUNTIME_COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"
BACKEND_ROOT = str(Path(__file__).resolve().parents[1])

_FULL_ENV = {
    "WSP_REMOTE_REPO_ROOT": "/root/repo",
    "WSP_REMOTE_JOB_ROOT": "/root/jobs",
    "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": RUNTIME_COMMIT,
    "WSP_REMOTE_SPACENET_ROOT": "/root/autodl-tmp/SpaceNet_Dataset",
    "WSP_REMOTE_DETECTOR_CHECKPOINT": "/root/models/det.pt",
    "WSP_REMOTE_FRN_CHECKPOINT": "/root/models/frn.pt",
    "WSP_REMOTE_FROZEN_CONFIG": "/root/models/frozen.json",
    "WSP_REMOTE_LS_STFT_NORMALIZATION": "/root/models/norm.json",
}


def _make_batch(batch_id="batch_x"):
    batch = RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=batch_id,
        required_remote_runtime_commit=RUNTIME_COMMIT,
        pipeline={"id": "pipeline_x", "version": "1.0"},
        asset_manifest_sha256="c" * 64,
        items=[RemoteExecutionItemV1(
            item_key="000000",
            request_id="req_1",
            local_run_id="run_x",
            orchestrator_commit=ORCHESTRATOR_COMMIT,
            recording=RemoteRecordingRefV1(
                dataset_name="SpaceNet", dataset_split="test", dataset_key="0",
                label_space="spacenet_14",
                expected_recording_fingerprint="a" * 64,
                expected_source_data_sha256="b" * 64,
            ),
            parameters={},
        )],
        request_sha256="0" * 64,
    )
    batch.request_sha256 = compute_request_sha256(batch)
    return batch


def _job_root(tmp_path, batch_id="batch_x"):
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs / batch_id


def _apply_env(monkeypatch):
    for name, value in _FULL_ENV.items():
        monkeypatch.setenv(name, value)


def test_cli_work_wires_production_executor(tmp_path, monkeypatch):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    _apply_env(monkeypatch)
    captured = {}

    def fake_run_work(batch_id, root, item_executor):
        captured["batch_id"] = batch_id
        captured["root"] = root
        captured["executor"] = item_executor

    monkeypatch.setattr(runner_module, "run_work", fake_run_work)
    args = SimpleNamespace(batch_id=batch.batch_id, job_root=str(job_root))
    rc = runner_module._cli_work(args)
    assert rc == 0
    assert captured["batch_id"] == batch.batch_id
    assert captured["root"] == job_root
    executor = captured["executor"]
    assert isinstance(executor, PluginItemExecutor)
    assert executor._batch == batch
    assert isinstance(executor._worker, RemoteWorkerContext)
    assert executor._trusted_assets_resolver == executor._worker.resolve_assets
    assert executor._runtime_descriptor == executor._worker.runtime_descriptor()
    assert executor._package_publisher is publish_package


def test_cli_work_fails_closed_on_missing_worker_env(tmp_path, monkeypatch):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    for name in _FULL_ENV:
        monkeypatch.delenv(name, raising=False)
    args = SimpleNamespace(batch_id=batch.batch_id, job_root=str(job_root))
    from app.core.errors import PlatformError

    with pytest.raises(PlatformError) as exc:
        runner_module._cli_work(args)
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_runner_work_lazy_import_does_not_import_torch_or_ultralytics(tmp_path):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.update(_FULL_ENV)
    code = f"""\
import sys
from types import SimpleNamespace
import app.remote_execution.runner as r

def fake_run_work(batch_id, job_root, item_executor):
    assert item_executor is not None, 'executor missing'
    assert 'torch' not in sys.modules, 'torch imported'
    assert 'ultralytics' not in sys.modules, 'ultralytics imported'

r.run_work = fake_run_work
rc = r._cli_work(SimpleNamespace(batch_id={batch.batch_id!r}, job_root={str(job_root)!r}))
assert rc == 0, rc
assert 'torch' not in sys.modules, 'torch imported'
assert 'ultralytics' not in sys.modules, 'ultralytics imported'
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr


def test_runner_module_import_still_gpu_free():
    code = (
        "import sys; import app.remote_execution.runner;"
        "assert 'torch' not in sys.modules, 'torch imported';"
        "assert 'ultralytics' not in sys.modules, 'ultralytics imported'"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
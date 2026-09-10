"""TASK D2B — plugin-native local inference worker (same contract as remote)."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import subprocess
import sys

import pytest

from app.analysis.local_inference_worker import execute_local_run, resolve_local_assets
from app.analysis.model import AnalysisRunModel
from app.core.config import Settings
from app.core.errors import PlatformError
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.labels.service import LabelSpaceService
from app.pipelines.base import PipelineDefinition, PipelineOutput
from app.recordings.model import RecordingModel
from app.remote_execution.assets import PipelineAssetManifest, compute_asset_manifest_sha256
from app.remote_execution.model_release import ModelRelease, ResolvedModelRelease
from app.remote_execution.runtime import RuntimeDescriptor
from app.storage.service import StorageService

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_ROOT = REPO_ROOT / "label_spaces"

_CPU_DESCRIPTOR = {
    "executor": "local_cpu",
    "device_type": "cpu",
    "device_index": None,
    "precision": "float32",
    "environment_ref": "/opt/ml/python",
    "environment_label": "local:gen-1",
}


@pytest.fixture(autouse=True)
def _local_runtime_generation(monkeypatch):
    """Worker requires the launching provider's immutable runtime generation."""
    monkeypatch.setenv("WSP_LOCAL_INFERENCE_RUNTIME_REF", "local:gen-1")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=REPO_ROOT,
        data_root=tmp_path / "data",
        label_space_root=LABEL_ROOT,
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
    )


def _database(settings: Settings) -> Database:
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    return database


def _definition(*, plugin_id: str, model_release_required: bool, label_space: str) -> PipelineDefinition:
    return PipelineDefinition(
        id=plugin_id,
        name="Local Test",
        version="1.0",
        label_space=label_space,
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_localization",
        executors_supported=("local_cpu",),
        recommended_executor="local_cpu",
        model_release_required=model_release_required,
    )


def _add_run(database: Database, *, run_id: str, pipeline_id: str, metadata: dict) -> None:
    with database.session_factory() as session:
        session.add(
            RecordingModel(
                id="rec_w",
                name="w",
                data_path="recordings/rec_w/raw.iq",
                data_format="float16_interleaved_le",
                source="custom",
                sample_rate_hz=1_000_000.0,
                center_frequency_hz=0.0,
                frequency_low_hz=-500_000.0,
                frequency_high_hz=500_000.0,
                num_samples=1000,
                duration_s=0.001,
                dataset_name="SpaceNet",
                dataset_split="test",
                label_space="spacenet_14",
                has_ground_truth=False,
            )
        )
        session.add(
            AnalysisRunModel(
                id=run_id,
                recording_id="rec_w",
                pipeline_id=pipeline_id,
                pipeline_version="1.0",
                executor="local_cpu",
                status="pending",
                parameters_json={},
                execution_metadata_json=metadata,
            )
        )
        session.commit()


class _FakeRuntime:
    def __init__(self, output: PipelineOutput | None = None) -> None:
        self.calls: list[tuple] = []
        self.output = output or PipelineOutput(detections=[])

    def execute(self, recording, parameters, workspace):
        self.calls.append((recording, parameters, workspace))
        return self.output


class _FakeHandle:
    def __init__(self, definition: PipelineDefinition) -> None:
        self.definition = definition
        self.load_runtime_calls: list[dict] = []
        self.runtime = _FakeRuntime()

    def load_runtime(self, *, assets, runtime_descriptor, output_label_space):
        self.load_runtime_calls.append(
            {
                "assets": assets,
                "runtime_descriptor": runtime_descriptor,
                "output_label_space": output_label_space,
            }
        )
        return self.runtime


class _FakeRegistry:
    def __init__(self, handle: _FakeHandle) -> None:
        self.handle = handle

    def get(self, plugin_id, plugin_version):
        assert (plugin_id, plugin_version) == (
            self.handle.definition.plugin_id,
            self.handle.definition.plugin_version,
        )
        return self.handle


class _FakeStore:
    def __init__(self, resolved: ResolvedModelRelease | None = None) -> None:
        self.resolved = resolved
        self.calls: list[tuple] = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.calls.append((plugin_id, plugin_version, requested))
        return self.resolved


def _run(database, settings, handle, store, run_id):
    return execute_local_run(
        run_id,
        settings,
        database=database,
        plugin_registry=_FakeRegistry(handle),
        label_service=LabelSpaceService(LABEL_ROOT),
        storage=StorageService(settings.data_root),
        model_release_store=store,
    )


def _status(database, run_id: str) -> AnalysisRunModel:
    with database.session_factory() as session:
        return session.get(AnalysisRunModel, run_id)


# ---------------------------------------------------------------------------
# Release-less / code-only path
# ---------------------------------------------------------------------------


def test_local_worker_release_less_executes_via_load_runtime(tmp_path):
    settings = _settings(tmp_path)
    database = _database(settings)
    definition = _definition(plugin_id="code_only", model_release_required=False, label_space="signal_presence_v1")
    _add_run(database, run_id="run_code", pipeline_id="code_only", metadata={"runtime_descriptor": _CPU_DESCRIPTOR})
    handle = _FakeHandle(definition)
    store = _FakeStore()

    _run(database, settings, handle, store, "run_code")

    # no ModelRelease is resolved or faked
    assert store.calls == []
    assert len(handle.load_runtime_calls) == 1
    call = handle.load_runtime_calls[0]
    assert call["assets"] == {}
    assert call["runtime_descriptor"] == RuntimeDescriptor.from_metadata(_CPU_DESCRIPTOR)
    assert call["output_label_space"].id == "signal_presence_v1"
    # execute receives the verified recording input, frozen parameters, workspace
    assert len(handle.runtime.calls) == 1
    recording_input, parameters, workspace = handle.runtime.calls[0]
    assert recording_input.id == "rec_w"
    assert parameters == {}
    assert workspace == StorageService(settings.data_root).artifact_dir("run_code")
    assert _status(database, "run_code").status == "completed"


def test_local_worker_release_less_rejects_model_release_metadata(tmp_path):
    settings = _settings(tmp_path)
    database = _database(settings)
    definition = _definition(plugin_id="code_only", model_release_required=False, label_space="signal_presence_v1")
    _add_run(
        database,
        run_id="run_bad",
        pipeline_id="code_only",
        metadata={"runtime_descriptor": _CPU_DESCRIPTOR, "model_release_id": "golden"},
    )
    with pytest.raises(PlatformError) as exc:
        _run(database, settings, _FakeHandle(definition), _FakeStore(), "run_bad")
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"
    run = _status(database, "run_bad")
    assert run.status == "failed"
    assert run.error_type == "MODEL_RELEASE_MISMATCH"


# ---------------------------------------------------------------------------
# Release-bound path
# ---------------------------------------------------------------------------


def _resolved_release(tmp_path: Path) -> tuple[ResolvedModelRelease, Path]:
    asset = tmp_path / "weights.bin"
    asset.write_bytes(b"weights")
    asset_sha = sha256(b"weights").hexdigest()
    provisional = PipelineAssetManifest(
        pipeline_id="released", pipeline_version="1.0", assets={"w": asset_sha}, asset_manifest_sha256="0" * 64
    )
    manifest = replace(provisional, asset_manifest_sha256=compute_asset_manifest_sha256(provisional))
    release = ModelRelease(
        plugin_id="released",
        plugin_version="1.0",
        model_release_id="golden",
        asset_manifest_path=tmp_path / "asset_manifest.json",
        asset_manifest_sha256=manifest.asset_manifest_sha256,
    )
    return ResolvedModelRelease(release=release, manifest=manifest), asset


def test_local_worker_release_bound_resolves_release_and_assets(tmp_path):
    settings = _settings(tmp_path)
    resolved, asset = _resolved_release(tmp_path)
    settings.local_asset_paths = {
        f"released/1.0/{resolved.manifest.asset_manifest_sha256}": {"w": str(asset)}
    }
    database = _database(settings)
    definition = _definition(plugin_id="released", model_release_required=True, label_space="spacenet_14")
    _add_run(
        database,
        run_id="run_rel",
        pipeline_id="released",
        metadata={
            "runtime_descriptor": _CPU_DESCRIPTOR,
            "model_release_id": "golden",
            "asset_manifest_sha256": resolved.manifest.asset_manifest_sha256,
        },
    )
    handle = _FakeHandle(definition)
    store = _FakeStore(resolved)

    _run(database, settings, handle, store, "run_rel")

    # authoritative resolve by exact frozen release id (never reverse lookup)
    assert store.calls == [("released", "1.0", "golden")]
    call = handle.load_runtime_calls[0]
    assert call["assets"] == {"w": asset}
    assert call["output_label_space"].id == "spacenet_14"
    assert _status(database, "run_rel").status == "completed"


def test_local_worker_rejects_release_manifest_mismatch(tmp_path):
    settings = _settings(tmp_path)
    resolved, asset = _resolved_release(tmp_path)
    settings.local_asset_paths = {
        f"released/1.0/{resolved.manifest.asset_manifest_sha256}": {"w": str(asset)}
    }
    database = _database(settings)
    definition = _definition(plugin_id="released", model_release_required=True, label_space="spacenet_14")
    _add_run(
        database,
        run_id="run_mismatch",
        pipeline_id="released",
        metadata={
            "runtime_descriptor": _CPU_DESCRIPTOR,
            "model_release_id": "golden",
            "asset_manifest_sha256": "b" * 64,
        },
    )
    with pytest.raises(PlatformError) as exc:
        _run(database, settings, _FakeHandle(definition), _FakeStore(resolved), "run_mismatch")
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"
    run = _status(database, "run_mismatch")
    assert run.status == "failed"
    assert run.error_type == "MODEL_RELEASE_MISMATCH"


# ---------------------------------------------------------------------------
# Descriptor failure
# ---------------------------------------------------------------------------


def test_local_worker_missing_descriptor_fails_typed(tmp_path):
    settings = _settings(tmp_path)
    database = _database(settings)
    definition = _definition(plugin_id="code_only", model_release_required=False, label_space="signal_presence_v1")
    _add_run(database, run_id="run_nod", pipeline_id="code_only", metadata={})
    with pytest.raises(PlatformError) as exc:
        _run(database, settings, _FakeHandle(definition), _FakeStore(), "run_nod")
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"


# ---------------------------------------------------------------------------
# resolve_local_assets
# ---------------------------------------------------------------------------


def test_resolve_local_assets_namespaced(tmp_path):
    config = {"p/1.0/" + "a" * 64: {"w": str(tmp_path / "w.bin")}}
    assets = resolve_local_assets(
        deployment_config=config,
        plugin_id="p",
        plugin_version="1.0",
        asset_manifest_sha256="a" * 64,
    )
    assert assets == {"w": tmp_path / "w.bin"}


def test_resolve_local_assets_missing_namespace_fails_closed():
    with pytest.raises(PlatformError) as exc:
        resolve_local_assets(
            deployment_config={},
            plugin_id="p",
            plugin_version="1.0",
            asset_manifest_sha256="a" * 64,
        )
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_resolve_local_assets_relative_path_fails_closed():
    config = {"p/1.0/" + "a" * 64: {"w": "relative/w.bin"}}
    with pytest.raises(PlatformError) as exc:
        resolve_local_assets(
            deployment_config=config,
            plugin_id="p",
            plugin_version="1.0",
            asset_manifest_sha256="a" * 64,
        )
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


# ---------------------------------------------------------------------------
# D2B FIX ROUND 1 — runtime generation binding + terminal immutability
# ---------------------------------------------------------------------------


def test_local_worker_requires_runtime_generation_env(tmp_path, monkeypatch):
    monkeypatch.delenv("WSP_LOCAL_INFERENCE_RUNTIME_REF", raising=False)
    settings = _settings(tmp_path)
    database = _database(settings)
    definition = _definition(plugin_id="code_only", model_release_required=False, label_space="signal_presence_v1")
    _add_run(database, run_id="run_nogen", pipeline_id="code_only", metadata={"runtime_descriptor": _CPU_DESCRIPTOR})
    with pytest.raises(PlatformError) as exc:
        _run(database, settings, _FakeHandle(definition), _FakeStore(), "run_nogen")
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"
    assert _status(database, "run_nogen").status == "failed"


def test_local_worker_rejects_runtime_generation_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("WSP_LOCAL_INFERENCE_RUNTIME_REF", "local:gen-2")
    settings = _settings(tmp_path)
    database = _database(settings)
    definition = _definition(plugin_id="code_only", model_release_required=False, label_space="signal_presence_v1")
    _add_run(database, run_id="run_gen", pipeline_id="code_only", metadata={"runtime_descriptor": _CPU_DESCRIPTOR})
    handle = _FakeHandle(definition)
    with pytest.raises(PlatformError) as exc:
        _run(database, settings, handle, _FakeStore(), "run_gen")
    assert exc.value.code == "RUNTIME_DESCRIPTOR_INVALID"
    # fail closed before any runtime construction/execution
    assert handle.load_runtime_calls == []
    assert handle.runtime.calls == []


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "interrupted"])
def test_local_worker_does_not_reexecute_terminal_run(tmp_path, terminal_status):
    settings = _settings(tmp_path)
    database = _database(settings)
    definition = _definition(plugin_id="code_only", model_release_required=False, label_space="signal_presence_v1")
    _add_run(database, run_id="run_terminal", pipeline_id="code_only", metadata={"runtime_descriptor": _CPU_DESCRIPTOR})
    with database.session_factory() as session:
        run = session.get(AnalysisRunModel, "run_terminal")
        run.status = terminal_status
        run.error_type = "KEEP_TYPE"
        run.error_message = "keep_message"
        session.commit()

    handle = _FakeHandle(definition)
    _run(database, settings, handle, _FakeStore(), "run_terminal")

    assert handle.load_runtime_calls == []
    assert handle.runtime.calls == []
    run = _status(database, "run_terminal")
    assert run.status == terminal_status
    assert run.error_type == "KEEP_TYPE"
    assert run.error_message == "keep_message"


def test_local_worker_pending_run_still_executes(tmp_path):
    settings = _settings(tmp_path)
    database = _database(settings)
    definition = _definition(plugin_id="code_only", model_release_required=False, label_space="signal_presence_v1")
    _add_run(database, run_id="run_pending", pipeline_id="code_only", metadata={"runtime_descriptor": _CPU_DESCRIPTOR})
    handle = _FakeHandle(definition)
    _run(database, settings, handle, _FakeStore(), "run_pending")
    assert len(handle.runtime.calls) == 1
    assert _status(database, "run_pending").status == "completed"


# ---------------------------------------------------------------------------
# Import boundary
# ---------------------------------------------------------------------------


def test_local_inference_worker_import_is_torch_free():
    code = (
        "import sys; import app.analysis.local_inference_worker; "
        "assert 'torch' not in sys.modules, 'torch leaked'; "
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT / "backend",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout

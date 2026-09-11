"""PRE-F1 — control-plane plugin source-of-truth unification.

The declarative ``PluginRegistry`` (``plugin_modules.json`` + the
``WISA_PLUGIN_MODULES`` seam) is the single source of truth for which plugins
exist. The compatibility ``PipelineRegistry`` catalog must be derived from those
declarations, with no concrete plugin imports/construction in control-plane code.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.pipelines.plugin_registry import create_plugin_registry
from app.pipelines.registry import create_pipeline_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ID = "zoomspec_yolo26n_aug_combined_frn_v3"

_CORE_FILES = (
    "backend/app/main.py",
    "backend/app/analysis/router.py",
    "backend/app/analysis/service.py",
    "backend/app/pipelines/registry.py",
    "backend/app/pipelines/plugin_registry.py",
    "backend/app/remote_execution/runner.py",
    "backend/app/remote_execution/plugin_executor.py",
    "backend/app/remote_execution/package_publisher.py",
)


def _write_synthetic_module(dirpath: Path, module_name: str, *, plugin_id: str,
                            version: str = "0.1") -> None:
    (dirpath / f"{module_name}.py").write_text(
        "from app.pipelines.base import PipelineDefinition\n"
        "from app.pipelines.plugin import PluginDeclaration\n"
        f"PLUGIN = PluginDeclaration(definition=PipelineDefinition(\n"
        f"    id={plugin_id!r}, name='Synthetic {plugin_id}', version={version!r},\n"
        f"    label_space='spacenet_14', recommended_device='CPU', cpu_supported=True,\n"
        "    stages=(), inspectable_stages=(),\n"
        "), runtime_factory_ref=None)\n",
        encoding="utf-8",
    )


def test_registry_module_has_no_concrete_plugin_imports():
    source = (REPO_ROOT / "backend" / "app" / "pipelines" / "registry.py").read_text()
    for forbidden in (
        "DummyPipeline", "STFTEnergyDetectorPipeline", "ZoomSpecRemoteOnlyPipeline",
        "app.pipelines.dummy", "app.pipelines.stft_energy",
        PLUGIN_ID,
    ):
        assert forbidden not in source, f"registry.py still references '{forbidden}'"


def test_catalog_derives_from_declarative_plugin_registry():
    from app.pipelines.plugin_registry import create_plugin_registry

    catalog = create_pipeline_registry()
    declared = {d.id for d in create_plugin_registry().list()}
    assert {d.id for d in catalog.list()} == declared
    assert {"dummy", "stft_energy_detector", PLUGIN_ID} <= declared


def test_synthetic_plugin_visible_via_discovery_seam(monkeypatch, tmp_path):
    _write_synthetic_module(tmp_path, "pref1_synthetic_mod", plugin_id="pref1_synthetic")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("WISA_PLUGIN_MODULES", "pref1_synthetic_mod")

    catalog = create_pipeline_registry()
    ids = [d.id for d in catalog.list()]
    assert "pref1_synthetic" in ids
    assert catalog.get("pref1_synthetic").definition.version == "0.1"


def test_removing_declaration_from_discovery_removes_it(monkeypatch, tmp_path):
    _write_synthetic_module(tmp_path, "pref1_synthetic_mod", plugin_id="pref1_synthetic")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("WISA_PLUGIN_MODULES", "pref1_synthetic_mod")
    assert "pref1_synthetic" in {d.id for d in create_pipeline_registry().list()}

    monkeypatch.delenv("WISA_PLUGIN_MODULES", raising=False)
    ids = {d.id for d in create_pipeline_registry().list()}
    assert "pref1_synthetic" not in ids
    with pytest.raises(PlatformError) as exc:
        create_pipeline_registry().get("pref1_synthetic")
    assert exc.value.code == "PIPELINE_INCOMPATIBLE"


def test_ambiguous_versions_for_one_id_fail_closed(monkeypatch, tmp_path):
    _write_synthetic_module(tmp_path, "pref1_dup_a", plugin_id="pref1_dup", version="1.0")
    _write_synthetic_module(tmp_path, "pref1_dup_b", plugin_id="pref1_dup", version="2.0")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("WISA_PLUGIN_MODULES", "pref1_dup_a,pref1_dup_b")

    with pytest.raises(PlatformError) as exc:
        create_pipeline_registry()
    assert exc.value.code == "PIPELINE_INCOMPATIBLE"


def test_analysis_service_resolves_declarative_definition(monkeypatch, tmp_path, session):
    from benchmark_fixture import add_recording

    from app.analysis.service import AnalysisService
    from executor_fixtures import FakeProvider, FakeRegistry

    _write_synthetic_module(tmp_path, "pref1_synthetic_mod", plugin_id="pref1_synthetic")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("WISA_PLUGIN_MODULES", "pref1_synthetic_mod")

    catalog = create_pipeline_registry()
    add_recording(session, recording_id="rec_pref1", name="0", label_space="spacenet_14")
    session.commit()

    service = AnalysisService(
        session, catalog, job_manager=object(),
        executor_registry=FakeRegistry({"local_cpu": FakeProvider("local_cpu")}),
    )
    availability = service.executor_availability(
        "rec_pref1", "pref1_synthetic", executor="local_cpu"
    )
    assert availability.available is True
    assert availability.executor == "local_cpu"


def test_api_pipelines_catalog_still_exposes_core_plugins(client):
    by_id = {item["id"]: item for item in client.get("/api/pipelines").json()}
    assert {"dummy", "stft_energy_detector", PLUGIN_ID} <= set(by_id)
    zoom = by_id[PLUGIN_ID]
    assert zoom["label_space"] == "spacenet_14"
    assert zoom["output_label_space"] == "spacenet_14"
    assert zoom["model_release_required"] is True


def test_core_files_have_no_plugin_id_branch():
    for rel in _CORE_FILES:
        source = (REPO_ROOT / rel).read_text()
        assert PLUGIN_ID not in source, f"{rel} contains plugin id branching"


def test_duplicate_exact_identity_fails_closed_both_discovery_orders(monkeypatch, tmp_path):
    """PRE-F1 fix: two distinct declaration modules with the SAME (id, version)
    must fail closed regardless of discovery order (no silent overwrite)."""
    _write_synthetic_module(tmp_path, "pref1_col_a", plugin_id="pref1_collision", version="1.0")
    _write_synthetic_module(tmp_path, "pref1_col_b", plugin_id="pref1_collision", version="1.0")
    monkeypatch.syspath_prepend(str(tmp_path))
    for order in ("pref1_col_a,pref1_col_b", "pref1_col_b,pref1_col_a"):
        monkeypatch.setenv("WISA_PLUGIN_MODULES", order)
        with pytest.raises(PlatformError) as exc:
            create_plugin_registry()
        assert exc.value.code == "PLUGIN_REGISTRY_CONFLICT"
        assert "pref1_collision" in exc.value.message
        assert "1.0" in exc.value.message

"""Plan B B2: acceptance-only operator configuration tests (control-plane only)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_common as common  # noqa: E402

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


def test_plan_b_asset_map_matches_manifests() -> None:
    from app.remote_execution.assets import load_pipeline_asset_manifest

    for plugin_id, manifest_path in common.MANIFEST_PATH.items():
        manifest = load_pipeline_asset_manifest(manifest_path)
        amap = common.plan_b_asset_map(plugin_id)
        assert len(amap) == 1
        namespace, logical = next(iter(amap.items()))
        assert namespace == f"{plugin_id}/{common.PLUGIN_VERSION[plugin_id]}/{manifest.asset_manifest_sha256}"
        assert set(logical) == set(manifest.assets)
        for path in logical.values():
            assert Path(path).is_absolute()


def test_bootstrap_env_sets_only_plan_b_keys(monkeypatch) -> None:
    snapshot = dict(os.environ)
    try:
        root = Path("/tmp/opencode/plan_b_env_test")
        common.bootstrap_env_before_app_import(root)
        for key in common.PLAN_B_WSP_KEYS:
            assert key in os.environ, key
        assert os.environ["WSP_RUNTIME_FAMILY"] == "autodl_primary"
        assert os.environ["WSP_LOCAL_GPU_RUNTIME_REF"] == common.GPU_RUNTIME_REF
        assert os.environ["WSP_LOCAL_INFERENCE_WORK_ROOT"] == str(root / "work")
        assert os.environ["WSP_DATA_ROOT"] == str(root / "data")
        assert os.environ["WSP_DATABASE_URL"] == f"sqlite:///{root / 'qual.db'}"
        assert os.environ["OMP_NUM_THREADS"] == "1"
        assert os.environ["MKL_NUM_THREADS"] == "1"
        # The asset map parses and covers both plugins.
        parsed = json.loads(os.environ["WSP_LOCAL_ASSET_PATHS_JSON"])
        assert any("cpn_bandwidth_tier" in key for key in parsed)
        assert any("zoomspec" in key for key in parsed)
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


def test_bootstrap_env_does_not_touch_profiles() -> None:
    source = (SCRIPTS / "plan_b_common.py").read_text(encoding="utf-8") + (
        SCRIPTS / "plan_b_env.py"
    ).read_text(encoding="utf-8")
    for forbidden in (".bashrc", ".profile", "/etc/environment", "os.system", "shell=True"):
        assert forbidden not in source


def test_verify_configuration_reports_identity() -> None:
    report = common.verify_configuration(collector=lambda: dict(BHQ3_MATERIAL))
    assert report["gpu_runtime_ref_derived"] == common.GPU_RUNTIME_REF
    assert report["gpu_runtime_ref_configured"] == common.GPU_RUNTIME_REF
    assert report["gpu_identity_match"] is True
    assert report["runtime_family"] == "autodl_primary"
    assert report["gpu_material"] == BHQ3_MATERIAL
    assert report["h2_stems"] == list(common.H2_STEMS)


def test_no_secrets_in_plan_b_tooling() -> None:
    source = (SCRIPTS / "plan_b_common.py").read_text(encoding="utf-8") + (
        SCRIPTS / "plan_b_env.py"
    ).read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in ("ssh", "passphrase", "begin openssh", "token"):
        assert forbidden not in lowered

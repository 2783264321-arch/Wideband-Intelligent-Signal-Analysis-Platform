"""Plan B acceptance-only operator configuration (B2).

Scoped / in-process environment only. This module never writes a shell profile,
never mutates server configuration, and never contains secrets. It is acceptance
tooling: production portability defaults are untouched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
SCRIPTS = Path(__file__).resolve().parent

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PLAN_B_ROOT = Path("/root/autodl-tmp/plan_b_qual")
EVIDENCE_DIR = PLAN_B_ROOT / "evidence"

RUNTIME_FAMILY = "autodl_primary"
ML_PYTHON = Path("/root/miniconda3/bin/python")
GPU_RUNTIME_REF = "local:autodl_primary:gpu:7b958347b5af"

SPACENET_ROOT = Path("/root/autodl-tmp/SpaceNet_Dataset/advanced")
DATASET_NAME = "SpaceNet"
DATASET_SPLIT = "test"
DATASET_LABEL_SPACE = "spacenet_14"

H2_STEMS = (
    "0", "1", "2", "3", "9", "11", "12", "15",
    "32", "42", "79", "80", "83", "99", "109", "280",
)

PLUGIN_VERSION = {
    "cpn_bandwidth_tier": "1.0.0",
    "zoomspec_yolo26n_aug_combined_frn_v3": "1.0.0",
}
MODEL_RELEASE = "golden"

ASSET_PATHS = {
    "detector_checkpoint": Path("/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt"),
    "ls_stft_normalization": Path("/root/autodl-tmp/release/support/normalization_ls_stft.json"),
    "frn_checkpoint": Path("/root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt"),
    "frozen_config": Path("/root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml"),
}

MANIFEST_PATH = {
    "cpn_bandwidth_tier": BACKEND / "app" / "pipelines" / "cpn_bandwidth_tier" / "asset_manifest.json",
    "zoomspec_yolo26n_aug_combined_frn_v3": (
        BACKEND / "app" / "pipelines" / "zoomspec_yolo26n_aug_combined_frn_v3" / "asset_manifest.json"
    ),
}

# The exact expected Plan-B WSP environment-key set (9 keys).
PLAN_B_WSP_KEYS = (
    "WSP_RUNTIME_FAMILY",
    "WSP_LOCAL_GPU_PYTHON_PATH",
    "WSP_LOCAL_GPU_RUNTIME_REF",
    "WSP_LOCAL_INFERENCE_WORK_ROOT",
    "WSP_LOCAL_ASSET_PATHS_JSON",
    "WSP_PROJECT_ROOT",
    "WSP_DATA_ROOT",
    "WSP_LABEL_SPACE_ROOT",
    "WSP_DATABASE_URL",
)

# Acceptance-only scoped thread override (never a global profile edit).
THREAD_ENV_OVERRIDE = {"OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}


def plan_b_asset_map(plugin_id: str) -> dict:
    """Build the trusted namespaced asset map for ``plugin_id`` from its committed
    manifest (never hardcoded logical names)."""
    from app.remote_execution.assets import load_pipeline_asset_manifest

    manifest = load_pipeline_asset_manifest(MANIFEST_PATH[plugin_id])
    namespace = f"{plugin_id}/{PLUGIN_VERSION[plugin_id]}/{manifest.asset_manifest_sha256}"
    return {namespace: {name: str(ASSET_PATHS[name]) for name in manifest.assets}}


def build_asset_map_json() -> str:
    merged: dict = {}
    for plugin_id in MANIFEST_PATH:
        merged.update(plan_b_asset_map(plugin_id))
    return json.dumps(merged)


def bootstrap_env_before_app_import(root: Path) -> None:
    """Set exactly PLAN_B_WSP_KEYS (plus the scoped thread override) in
    ``os.environ`` for THIS PROCESS ONLY.

    Must be called before importing ``app.main`` / ``create_app`` so spawned
    coordinator and worker subprocesses inherit it (JobManager copies
    ``os.environ``). Never writes a shell profile.
    """
    root = Path(root)
    env = {
        "WSP_RUNTIME_FAMILY": RUNTIME_FAMILY,
        "WSP_LOCAL_GPU_PYTHON_PATH": str(ML_PYTHON),
        "WSP_LOCAL_GPU_RUNTIME_REF": GPU_RUNTIME_REF,
        "WSP_LOCAL_INFERENCE_WORK_ROOT": str(root / "work"),
        "WSP_LOCAL_ASSET_PATHS_JSON": build_asset_map_json(),
        "WSP_PROJECT_ROOT": str(REPO),
        "WSP_DATA_ROOT": str(root / "data"),
        "WSP_LABEL_SPACE_ROOT": str(REPO / "label_spaces"),
        "WSP_DATABASE_URL": f"sqlite:///{root / 'qual.db'}",
    }
    env.update(THREAD_ENV_OVERRIDE)
    os.environ.update(env)


def build_settings(root: Path):
    """Return ``Settings()`` after the Plan-B environment is applied."""
    bootstrap_env_before_app_import(root)
    from app.core.config import Settings

    return Settings()


def _collect_gpu_material() -> dict:
    from app.runtime_qualification.identity import BHQ3_GPU_V1, collect_identity_material

    return collect_identity_material(ML_PYTHON, scheme=BHQ3_GPU_V1)


def verify_configuration(collector=None) -> dict:
    """Read-only Plan-B configuration verification. Never prints secrets."""
    from app.runtime_qualification.identity import (
        BHQ3_GPU_V1,
        derive_generation_for_scheme,
        derive_local_runtime_ref,
    )

    material = (collector or _collect_gpu_material)()
    generation = derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=material)
    derived_ref = derive_local_runtime_ref(
        family=RUNTIME_FAMILY, kind="gpu", generation=generation
    )

    namespaces = {}
    assets_ok = True
    for plugin_id in MANIFEST_PATH:
        amap = plan_b_asset_map(plugin_id)
        namespaces.update(amap)
        for logical, path in next(iter(amap.values())).items():
            if not Path(path).is_file():
                assets_ok = False

    report = {
        "runtime_family": RUNTIME_FAMILY,
        "interpreter": str(ML_PYTHON),
        "interpreter_exists": ML_PYTHON.is_file(),
        "work_root": str(PLAN_B_ROOT),
        "gpu_runtime_ref_configured": GPU_RUNTIME_REF,
        "gpu_runtime_ref_derived": derived_ref,
        "gpu_identity_match": derived_ref == GPU_RUNTIME_REF,
        "gpu_material": material,
        "asset_namespaces": sorted(namespaces),
        "assets_present": assets_ok,
        "wsp_keys_expected": list(PLAN_B_WSP_KEYS),
        "space_net_root": str(SPACENET_ROOT),
        "space_net_present": SPACENET_ROOT.is_dir(),
        "h2_stems": list(H2_STEMS),
    }
    return report

"""TASK B5 — ZoomSpec golden ModelRelease + platform default (data only).

B5 adds no logic; it pins the golden release record and default selection so the
plugin-agnostic control-plane resolution has a verified ZoomSpec target before B4
removes the literal manifest path.
"""

import json
from pathlib import Path

from app.remote_execution.assets import load_pipeline_asset_manifest
from app.remote_execution.model_release import (
    ModelReleaseStore,
    load_model_release_defaults,
)

GOLDEN_SHA = "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08"
PLUGIN_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
PLUGIN_VERSION = "1.0.0"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_ROOT = _REPO_ROOT / "backend" / "app" / "pipelines"
_MANIFEST_PATH = _PLUGINS_ROOT / PLUGIN_ID / "asset_manifest.json"
_DEFAULTS_PATH = _PLUGINS_ROOT / "model_release_defaults.json"


def _store() -> ModelReleaseStore:
    return ModelReleaseStore(_PLUGINS_ROOT, load_model_release_defaults(_DEFAULTS_PATH))


def test_golden_release_discovered_and_hash_matches():
    store = _store()
    releases = store.list_releases(PLUGIN_ID, PLUGIN_VERSION)
    assert [release.model_release_id for release in releases] == ["golden"]

    golden = store.get(PLUGIN_ID, PLUGIN_VERSION, "golden")
    assert golden.plugin_id == PLUGIN_ID
    assert golden.plugin_version == PLUGIN_VERSION
    assert golden.model_release_id == "golden"
    assert golden.asset_manifest_sha256 == GOLDEN_SHA
    assert golden.asset_manifest_path == _MANIFEST_PATH.resolve()


def test_default_resolves_to_golden():
    store = _store()
    assert store.default_release_id(PLUGIN_ID, PLUGIN_VERSION) == "golden"

    resolved = store.resolve(PLUGIN_ID, PLUGIN_VERSION, None)
    assert resolved.release.model_release_id == "golden"
    assert resolved.release.asset_manifest_sha256 == GOLDEN_SHA
    assert resolved.manifest.asset_manifest_sha256 == GOLDEN_SHA
    assert resolved.manifest.pipeline_id == PLUGIN_ID
    assert resolved.manifest.pipeline_version == PLUGIN_VERSION

    explicit = store.resolve(PLUGIN_ID, PLUGIN_VERSION, "golden")
    assert explicit.release.model_release_id == "golden"
    assert explicit.manifest.asset_manifest_sha256 == GOLDEN_SHA


def test_golden_manifest_payload_still_v1():
    raw = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert set(raw) == {"pipeline_id", "pipeline_version", "assets", "asset_manifest_sha256"}
    assert "model_release_id" not in raw
    assert raw["asset_manifest_sha256"] == GOLDEN_SHA

    manifest = load_pipeline_asset_manifest(_MANIFEST_PATH)
    assert manifest.asset_manifest_sha256 == GOLDEN_SHA

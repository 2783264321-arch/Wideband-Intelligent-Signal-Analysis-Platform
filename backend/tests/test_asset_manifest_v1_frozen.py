"""TASK B2 — freeze the AssetManifest V1 golden identity."""

import json
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.remote_execution.assets import (
    PipelineAssetManifest,
    load_manifest_for_release,
    load_pipeline_asset_manifest,
)

GOLDEN_SHA256 = "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08"
TRACKED_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "app"
    / "pipelines"
    / "zoomspec_yolo26n_aug_combined_frn_v3"
    / "asset_manifest.json"
)


def test_golden_manifest_hash_unchanged():
    manifest = load_pipeline_asset_manifest(TRACKED_MANIFEST_PATH)
    assert manifest.asset_manifest_sha256 == GOLDEN_SHA256


def test_manifest_payload_has_no_model_release_id():
    raw = json.loads(TRACKED_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "model_release_id" not in raw
    assert set(raw) == {"pipeline_id", "pipeline_version", "assets", "asset_manifest_sha256"}


def test_load_manifest_for_release_accepts_golden_identity():
    manifest = load_manifest_for_release(TRACKED_MANIFEST_PATH, GOLDEN_SHA256)
    assert isinstance(manifest, PipelineAssetManifest)
    assert manifest.asset_manifest_sha256 == GOLDEN_SHA256


def test_load_manifest_for_release_rejects_mismatched_hash():
    with pytest.raises(PlatformError) as excinfo:
        load_manifest_for_release(TRACKED_MANIFEST_PATH, "a" * 64)
    assert excinfo.value.code == "PIPELINE_ASSET_MISMATCH"


def test_load_manifest_for_release_propagates_strict_load_failure(tmp_path):
    invalid = tmp_path / "asset_manifest.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(PlatformError) as excinfo:
        load_manifest_for_release(invalid, GOLDEN_SHA256)
    assert excinfo.value.code == "PIPELINE_ASSET_MISMATCH"

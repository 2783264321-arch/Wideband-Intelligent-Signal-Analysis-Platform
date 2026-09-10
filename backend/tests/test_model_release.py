"""TASK B1 — ModelRelease store, default resolution, and reverse lookup."""

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.remote_execution.assets import PipelineAssetManifest, compute_asset_manifest_sha256
from app.remote_execution.model_release import (
    ModelRelease,
    ModelReleaseStore,
    ResolvedModelRelease,
    load_model_release_defaults,
)

_PLUGIN_ID = "demo_plugin"
_PLUGIN_VERSION = "1.0.0"


def _manifest(
    pipeline_id: str = _PLUGIN_ID,
    pipeline_version: str = _PLUGIN_VERSION,
    seed: str = "",
) -> PipelineAssetManifest:
    provisional = PipelineAssetManifest(
        pipeline_id=pipeline_id,
        pipeline_version=pipeline_version,
        assets={"weights": sha256(f"weights:{seed}".encode()).hexdigest()},
        asset_manifest_sha256="0" * 64,
    )
    return replace(provisional, asset_manifest_sha256=compute_asset_manifest_sha256(provisional))


def _write_manifest(path: Path, manifest: PipelineAssetManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pipeline_id": manifest.pipeline_id,
                "pipeline_version": manifest.pipeline_version,
                "assets": manifest.assets,
                "asset_manifest_sha256": manifest.asset_manifest_sha256,
            }
        ),
        encoding="utf-8",
    )


def _write_release(
    package_root: Path,
    release_id: str,
    *,
    manifest_path: str,
    manifest_sha256: str,
    plugin_id: str = _PLUGIN_ID,
    plugin_version: str = _PLUGIN_VERSION,
) -> None:
    releases_dir = package_root / "model_releases"
    releases_dir.mkdir(parents=True, exist_ok=True)
    (releases_dir / f"{release_id}.json").write_text(
        json.dumps(
            {
                "plugin_id": plugin_id,
                "plugin_version": plugin_version,
                "model_release_id": release_id,
                "asset_manifest_path": manifest_path,
                "asset_manifest_sha256": manifest_sha256,
            }
        ),
        encoding="utf-8",
    )


def _write_defaults(path: Path, defaults: dict) -> None:
    path.write_text(json.dumps({"defaults": defaults}), encoding="utf-8")


def _package_with_release(tmp_path: Path, release_id: str = "golden", manifest_name: str = "asset_manifest.json") -> tuple[Path, Path, PipelineAssetManifest]:
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    manifest = _manifest()
    _write_manifest(package_root / manifest_name, manifest)
    _write_release(package_root, release_id, manifest_path=manifest_name, manifest_sha256=manifest.asset_manifest_sha256)
    return plugins_root, package_root, manifest


def test_load_defaults_and_resolve_default(tmp_path):
    defaults_path = tmp_path / "model_release_defaults.json"
    _write_defaults(defaults_path, {_PLUGIN_ID: {_PLUGIN_VERSION: "golden"}})
    defaults = load_model_release_defaults(defaults_path)
    assert defaults == {(_PLUGIN_ID, _PLUGIN_VERSION): "golden"}

    plugins_root, _, manifest = _package_with_release(tmp_path)
    store = ModelReleaseStore(plugins_root, defaults)
    assert store.default_release_id(_PLUGIN_ID, _PLUGIN_VERSION) == "golden"

    resolved = store.resolve(_PLUGIN_ID, _PLUGIN_VERSION, None)
    assert isinstance(resolved, ResolvedModelRelease)
    assert isinstance(resolved.release, ModelRelease)
    assert resolved.release.model_release_id == "golden"
    assert resolved.release.asset_manifest_sha256 == manifest.asset_manifest_sha256
    assert resolved.manifest.asset_manifest_sha256 == manifest.asset_manifest_sha256


def test_load_defaults_empty_object(tmp_path):
    defaults_path = tmp_path / "model_release_defaults.json"
    defaults_path.write_text('{"defaults": {}}', encoding="utf-8")
    assert load_model_release_defaults(defaults_path) == {}


def test_resolve_explicit_release_overrides_default(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    golden = _manifest(seed="golden")
    tuned = _manifest(seed="tuned")
    _write_manifest(package_root / "golden_manifest.json", golden)
    _write_manifest(package_root / "tuned_manifest.json", tuned)
    _write_release(package_root, "golden", manifest_path="golden_manifest.json", manifest_sha256=golden.asset_manifest_sha256)
    _write_release(package_root, "tuned", manifest_path="tuned_manifest.json", manifest_sha256=tuned.asset_manifest_sha256)
    store = ModelReleaseStore(plugins_root, {(_PLUGIN_ID, _PLUGIN_VERSION): "golden"})

    assert store.resolve(_PLUGIN_ID, _PLUGIN_VERSION, None).release.model_release_id == "golden"
    assert store.resolve(_PLUGIN_ID, _PLUGIN_VERSION, "tuned").release.model_release_id == "tuned"


def test_unknown_release_raises_not_found(tmp_path):
    plugins_root, _, _ = _package_with_release(tmp_path)
    store = ModelReleaseStore(plugins_root, {})
    with pytest.raises(PlatformError) as excinfo:
        store.get(_PLUGIN_ID, _PLUGIN_VERSION, "missing")
    assert excinfo.value.code == "MODEL_RELEASE_NOT_FOUND"
    with pytest.raises(PlatformError) as resolve_excinfo:
        store.resolve(_PLUGIN_ID, _PLUGIN_VERSION, None)
    assert resolve_excinfo.value.code == "MODEL_RELEASE_NOT_FOUND"


def test_manifest_hash_mismatch_raises(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    manifest = _manifest()
    _write_manifest(package_root / "asset_manifest.json", manifest)
    _write_release(
        package_root,
        "golden",
        manifest_path="asset_manifest.json",
        manifest_sha256="f" * 64,
    )
    store = ModelReleaseStore(plugins_root, {(_PLUGIN_ID, _PLUGIN_VERSION): "golden"})
    with pytest.raises(PlatformError) as excinfo:
        store.resolve(_PLUGIN_ID, _PLUGIN_VERSION, None)
    assert excinfo.value.code == "MODEL_RELEASE_MISMATCH"


def test_release_manifest_identity_mismatch_raises(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    foreign = _manifest(pipeline_id="other_plugin")
    _write_manifest(package_root / "asset_manifest.json", foreign)
    _write_release(package_root, "golden", manifest_path="asset_manifest.json", manifest_sha256=foreign.asset_manifest_sha256)
    store = ModelReleaseStore(plugins_root, {(_PLUGIN_ID, _PLUGIN_VERSION): "golden"})
    with pytest.raises(PlatformError) as excinfo:
        store.resolve(_PLUGIN_ID, _PLUGIN_VERSION, None)
    assert excinfo.value.code == "MODEL_RELEASE_MISMATCH"


def test_resolve_by_manifest_sha_roundtrip(tmp_path):
    plugins_root, _, manifest = _package_with_release(tmp_path)
    store = ModelReleaseStore(plugins_root, {})
    release = store.resolve_by_manifest_sha(_PLUGIN_ID, _PLUGIN_VERSION, manifest.asset_manifest_sha256)
    assert release.model_release_id == "golden"


def test_resolve_by_manifest_sha_unknown_raises(tmp_path):
    plugins_root, _, _ = _package_with_release(tmp_path)
    store = ModelReleaseStore(plugins_root, {})
    with pytest.raises(PlatformError) as excinfo:
        store.resolve_by_manifest_sha(_PLUGIN_ID, _PLUGIN_VERSION, "a" * 64)
    assert excinfo.value.code == "MODEL_RELEASE_MISMATCH"


def test_two_release_ids_may_share_one_manifest_hash(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    shared = _manifest()
    _write_manifest(package_root / "asset_manifest.json", shared)
    _write_release(package_root, "golden", manifest_path="asset_manifest.json", manifest_sha256=shared.asset_manifest_sha256)
    _write_release(package_root, "alias", manifest_path="asset_manifest.json", manifest_sha256=shared.asset_manifest_sha256)
    store = ModelReleaseStore(plugins_root, {})

    assert {release.model_release_id for release in store.list_releases(_PLUGIN_ID, _PLUGIN_VERSION)} == {"golden", "alias"}
    assert store.resolve(_PLUGIN_ID, _PLUGIN_VERSION, "golden").release.model_release_id == "golden"
    assert store.resolve(_PLUGIN_ID, _PLUGIN_VERSION, "alias").release.model_release_id == "alias"
    with pytest.raises(PlatformError) as excinfo:
        store.resolve_by_manifest_sha(_PLUGIN_ID, _PLUGIN_VERSION, shared.asset_manifest_sha256)
    assert excinfo.value.code == "MODEL_RELEASE_MISMATCH"


def test_manifest_path_outside_package_rejected(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    outside = tmp_path / "outside" / "asset_manifest.json"
    manifest = _manifest()
    _write_manifest(outside, manifest)
    _write_release(package_root, "golden", manifest_path=str(outside.resolve()), manifest_sha256=manifest.asset_manifest_sha256)
    with pytest.raises(PlatformError) as excinfo:
        ModelReleaseStore(plugins_root, {})
    assert excinfo.value.code == "MODEL_RELEASE_MISMATCH"


def test_manifest_path_parent_escape_rejected(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    manifest = _manifest()
    _write_manifest(plugins_root / "asset_manifest.json", manifest)
    _write_release(package_root, "golden", manifest_path="../asset_manifest.json", manifest_sha256=manifest.asset_manifest_sha256)
    with pytest.raises(PlatformError) as excinfo:
        ModelReleaseStore(plugins_root, {})
    assert excinfo.value.code == "MODEL_RELEASE_MISMATCH"


def test_manifest_path_symlink_escape_rejected(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    manifest = _manifest()
    outside = tmp_path / "outside" / "asset_manifest.json"
    _write_manifest(outside, manifest)
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "link_manifest.json").symlink_to(outside.resolve())
    _write_release(package_root, "golden", manifest_path="link_manifest.json", manifest_sha256=manifest.asset_manifest_sha256)
    with pytest.raises(PlatformError) as excinfo:
        ModelReleaseStore(plugins_root, {})
    assert excinfo.value.code == "MODEL_RELEASE_MISMATCH"


def test_release_filename_must_match_release_id(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    manifest = _manifest()
    _write_manifest(package_root / "asset_manifest.json", manifest)
    releases_dir = package_root / "model_releases"
    releases_dir.mkdir(parents=True, exist_ok=True)
    (releases_dir / "wrong_name.json").write_text(
        json.dumps(
            {
                "plugin_id": _PLUGIN_ID,
                "plugin_version": _PLUGIN_VERSION,
                "model_release_id": "golden",
                "asset_manifest_path": "asset_manifest.json",
                "asset_manifest_sha256": manifest.asset_manifest_sha256,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PlatformError) as excinfo:
        ModelReleaseStore(plugins_root, {})
    assert excinfo.value.code == "MODEL_RELEASE_MISMATCH"


def test_list_releases_scopes_to_plugin_version(tmp_path):
    plugins_root = tmp_path / "plugins"
    package_root = plugins_root / _PLUGIN_ID
    manifest = _manifest()
    _write_manifest(package_root / "asset_manifest.json", manifest)
    _write_release(package_root, "golden", manifest_path="asset_manifest.json", manifest_sha256=manifest.asset_manifest_sha256)
    _write_release(
        package_root,
        "next",
        manifest_path="asset_manifest.json",
        manifest_sha256=manifest.asset_manifest_sha256,
        plugin_version="2.0.0",
    )
    store = ModelReleaseStore(plugins_root, {})
    assert [r.model_release_id for r in store.list_releases(_PLUGIN_ID, _PLUGIN_VERSION)] == ["golden"]
    assert [r.model_release_id for r in store.list_releases(_PLUGIN_ID, "2.0.0")] == ["next"]

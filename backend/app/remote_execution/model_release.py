"""M9.2-B ModelRelease records: plugin-code version vs trained-asset version.

A ``ModelRelease`` binds ``(plugin_id, plugin_version, model_release_id)`` to a
frozen AssetManifest V1 (by hash). Release records are platform data discovered
from ``<plugins_root>/<plugin_pkg>/model_releases/*.json``; the manifest path in
a record is plugin-package-relative and never comes from the wire.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping

from app.core.errors import PlatformError
from app.remote_execution.assets import PipelineAssetManifest, load_pipeline_asset_manifest

_RELEASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_RELEASE_FIELDS = (
    "plugin_id",
    "plugin_version",
    "model_release_id",
    "asset_manifest_path",
    "asset_manifest_sha256",
)


def _mismatch(message: str) -> PlatformError:
    return PlatformError("MODEL_RELEASE_MISMATCH", message)


def _not_found(message: str) -> PlatformError:
    return PlatformError("MODEL_RELEASE_NOT_FOUND", message)


@dataclass(frozen=True)
class ModelRelease:
    plugin_id: str
    plugin_version: str
    model_release_id: str
    asset_manifest_path: Path  # absolute, resolved inside the plugin package
    asset_manifest_sha256: str


@dataclass(frozen=True)
class ResolvedModelRelease:
    release: ModelRelease
    manifest: PipelineAssetManifest


class ModelReleaseStore:
    """Discovers and resolves immutable ModelRelease records for plugins."""

    def __init__(self, plugins_root: Path, defaults: Mapping[tuple[str, str], str]) -> None:
        self._plugins_root = Path(plugins_root)
        self._defaults = dict(defaults)
        self._releases: dict[tuple[str, str, str], ModelRelease] = {}
        self._discover()

    def _discover(self) -> None:
        if not self._plugins_root.is_dir():
            return
        for package_root in sorted(self._plugins_root.iterdir()):
            releases_dir = package_root / "model_releases"
            if not package_root.is_dir() or not releases_dir.is_dir():
                continue
            for record_path in sorted(releases_dir.glob("*.json")):
                release = self._load_record(record_path, package_root)
                key = (release.plugin_id, release.plugin_version, release.model_release_id)
                if key in self._releases:
                    raise _mismatch(f"Duplicate model release identity {key}.")
                self._releases[key] = release

    def _load_record(self, record_path: Path, package_root: Path) -> ModelRelease:
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise _mismatch("ModelRelease record could not be read.") from exc
        except (ValueError, TypeError) as exc:
            raise _mismatch("ModelRelease record JSON is invalid.") from exc
        if not isinstance(payload, dict):
            raise _mismatch("ModelRelease record must be a JSON object.")
        if any(field not in payload for field in _RELEASE_FIELDS):
            raise _mismatch("ModelRelease record is missing required fields.")

        plugin_id = payload["plugin_id"]
        plugin_version = payload["plugin_version"]
        model_release_id = payload["model_release_id"]
        asset_manifest_path = payload["asset_manifest_path"]
        asset_manifest_sha256 = payload["asset_manifest_sha256"]
        for value in (plugin_id, plugin_version, model_release_id, asset_manifest_path, asset_manifest_sha256):
            if not isinstance(value, str) or not value:
                raise _mismatch("ModelRelease record fields must be non-empty strings.")
        if _RELEASE_ID_RE.fullmatch(model_release_id) is None:
            raise _mismatch(f"Invalid model_release_id '{model_release_id}'.")
        if _SHA256_RE.fullmatch(asset_manifest_sha256) is None:
            raise _mismatch("asset_manifest_sha256 must be a 64-character lowercase SHA256.")

        if package_root.name != plugin_id:
            raise _mismatch("ModelRelease plugin_id does not match its package directory.")
        if record_path.stem != model_release_id:
            raise _mismatch("ModelRelease file name does not match model_release_id.")

        resolved_manifest_path = self._resolve_manifest_path(asset_manifest_path, package_root)
        return ModelRelease(
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            model_release_id=model_release_id,
            asset_manifest_path=resolved_manifest_path,
            asset_manifest_sha256=asset_manifest_sha256,
        )

    @staticmethod
    def _resolve_manifest_path(asset_manifest_path: str, package_root: Path) -> Path:
        candidate = Path(asset_manifest_path)
        if candidate.is_absolute():
            raise _mismatch("asset_manifest_path must be relative to the plugin package.")
        resolved_root = package_root.resolve()
        resolved = (resolved_root / candidate).resolve()
        if not resolved.is_relative_to(resolved_root):
            raise _mismatch("asset_manifest_path escapes the plugin package.")
        return resolved

    def list_releases(self, plugin_id: str, plugin_version: str) -> list[ModelRelease]:
        return [
            release
            for key, release in sorted(self._releases.items())
            if key[0] == plugin_id and key[1] == plugin_version
        ]

    def get(self, plugin_id: str, plugin_version: str, model_release_id: str) -> ModelRelease:
        release = self._releases.get((plugin_id, plugin_version, model_release_id))
        if release is None:
            raise _not_found(
                f"Model release '{model_release_id}' is not registered for "
                f"'{plugin_id}' version '{plugin_version}'."
            )
        return release

    def default_release_id(self, plugin_id: str, plugin_version: str) -> str | None:
        return self._defaults.get((plugin_id, plugin_version))

    def resolve(self, plugin_id: str, plugin_version: str, requested: str | None) -> ResolvedModelRelease:
        release_id = requested or self.default_release_id(plugin_id, plugin_version)
        if release_id is None:
            raise _not_found(f"No model release requested or defaulted for '{plugin_id}' version '{plugin_version}'.")
        release = self.get(plugin_id, plugin_version, release_id)
        manifest = load_pipeline_asset_manifest(release.asset_manifest_path)
        if manifest.pipeline_id != plugin_id or manifest.pipeline_version != plugin_version:
            raise _mismatch("AssetManifest identity does not match the ModelRelease.")
        if manifest.asset_manifest_sha256 != release.asset_manifest_sha256:
            raise _mismatch("AssetManifest self-hash does not match the ModelRelease record.")
        return ResolvedModelRelease(release=release, manifest=manifest)

    def resolve_by_manifest_sha(self, plugin_id: str, plugin_version: str, asset_manifest_sha256: str) -> ModelRelease:
        matches = [
            release
            for key, release in sorted(self._releases.items())
            if key[0] == plugin_id
            and key[1] == plugin_version
            and release.asset_manifest_sha256 == asset_manifest_sha256
        ]
        if len(matches) != 1:
            raise _mismatch(
                f"Expected exactly one model release for manifest '{asset_manifest_sha256}' "
                f"under '{plugin_id}' version '{plugin_version}', found {len(matches)}."
            )
        return matches[0]


def load_model_release_defaults(path: Path) -> dict[tuple[str, str], str]:
    """Load ``{"defaults": {plugin_id: {plugin_version: release_id}}}``."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise _mismatch("model release defaults could not be read.") from exc
    except (ValueError, TypeError) as exc:
        raise _mismatch("model release defaults JSON is invalid.") from exc
    if not isinstance(payload, dict):
        raise _mismatch("model release defaults must be a JSON object.")
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        raise _mismatch("model release defaults must contain a 'defaults' object.")
    result: dict[tuple[str, str], str] = {}
    for plugin_id, versions in defaults.items():
        if not isinstance(plugin_id, str) or not plugin_id:
            raise _mismatch("default plugin_id must be a non-empty string.")
        if not isinstance(versions, dict):
            raise _mismatch("default plugin versions must be an object.")
        for plugin_version, release_id in versions.items():
            if not isinstance(plugin_version, str) or not plugin_version:
                raise _mismatch("default plugin_version must be a non-empty string.")
            if not isinstance(release_id, str) or not release_id:
                raise _mismatch("default model_release_id must be a non-empty string.")
            result[(plugin_id, plugin_version)] = release_id
    return result

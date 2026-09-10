"""M9.1-B Task 10 frozen pipeline asset manifest + fail-closed verification.

Records immutable logical asset identities (SHA256) for the frozen pipeline and
verifies the deployed runtime/checkpoints without ever loading a model.

- ``asset_manifest_sha256`` is the canonical hash of the manifest record EXCLUDING
  the self field (no self-referential hash).
- Only logical asset names + SHA256 identities are stored in Git; absolute
  deployment paths are deployment configuration and never enter this module.
- Runtime verification is read-only ``git rev-parse HEAD``; no Git mutation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from pathlib import Path
import subprocess

from app.benchmarks.manifest import canonical_json_bytes
from app.core.errors import PlatformError
from app.remote_execution.source_hash import compute_file_sha256

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = ("pipeline_id", "pipeline_version", "assets", "asset_manifest_sha256")


def _asset_error(message: str) -> PlatformError:
    return PlatformError("PIPELINE_ASSET_MISMATCH", message)


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str):
    raise ValueError(f"non-finite JSON constant: {value!r}")


@dataclass(frozen=True)
class PipelineAssetManifest:
    pipeline_id: str
    pipeline_version: str
    assets: dict[str, str]
    asset_manifest_sha256: str


def canonical_asset_manifest_payload(manifest: PipelineAssetManifest) -> dict:
    """Explicit canonical manifest identity; excludes ``asset_manifest_sha256``."""
    return {
        "pipeline_id": manifest.pipeline_id,
        "pipeline_version": manifest.pipeline_version,
        "assets": dict(manifest.assets),
    }


def compute_asset_manifest_sha256(manifest: PipelineAssetManifest) -> str:
    payload = canonical_asset_manifest_payload(manifest)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _validate_sha256(value: str, name: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise _asset_error(f"{name} is not a valid 64-character lowercase SHA256.")


def _validate_manifest(manifest: PipelineAssetManifest) -> None:
    if not isinstance(manifest.pipeline_id, str):
        raise _asset_error("pipeline_id must be a string.")
    if not isinstance(manifest.pipeline_version, str):
        raise _asset_error("pipeline_version must be a string.")
    if not isinstance(manifest.assets, dict):
        raise _asset_error("assets must be an object.")
    if not isinstance(manifest.asset_manifest_sha256, str):
        raise _asset_error("asset_manifest_sha256 must be a string.")
    if not manifest.pipeline_id or not manifest.pipeline_version:
        raise _asset_error("pipeline id/version must be non-empty.")
    for key, value in manifest.assets.items():
        if not isinstance(key, str) or not key:
            raise _asset_error("asset logical name must be a non-empty string.")
        if not isinstance(value, str):
            raise _asset_error(f"asset '{key}' SHA256 must be a string.")
        _validate_sha256(value, f"asset '{key}'")
    _validate_sha256(manifest.asset_manifest_sha256, "asset_manifest_sha256")
    computed = compute_asset_manifest_sha256(manifest)
    if computed != manifest.asset_manifest_sha256:
        raise _asset_error("asset manifest self-hash does not match the canonical payload.")


def load_pipeline_asset_manifest(path: Path) -> PipelineAssetManifest:
    """Strictly load a tracked manifest JSON (duplicate keys / non-finite
    constants / non-object rejected)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _asset_error("asset manifest could not be read.") from exc
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (ValueError, TypeError):
        raise _asset_error("asset manifest JSON is invalid.")
    if not isinstance(payload, dict):
        raise _asset_error("asset manifest must be a JSON object.")
    if set(payload) != set(_MANIFEST_FIELDS):
        raise _asset_error("asset manifest contains unknown or missing fields.")
    if not isinstance(payload["pipeline_id"], str):
        raise _asset_error("pipeline_id must be a JSON string.")
    if not isinstance(payload["pipeline_version"], str):
        raise _asset_error("pipeline_version must be a JSON string.")
    if not isinstance(payload["assets"], dict):
        raise _asset_error("assets must be a JSON object.")
    if not isinstance(payload["asset_manifest_sha256"], str):
        raise _asset_error("asset_manifest_sha256 must be a JSON string.")
    for key, value in payload["assets"].items():
        if not isinstance(key, str):
            raise _asset_error("asset logical names must be JSON strings.")
        if not isinstance(value, str):
            raise _asset_error(f"asset '{key}' SHA256 must be a JSON string.")
    manifest = PipelineAssetManifest(
        pipeline_id=payload["pipeline_id"],
        pipeline_version=payload["pipeline_version"],
        assets=dict(payload["assets"]),
        asset_manifest_sha256=payload["asset_manifest_sha256"],
    )
    _validate_manifest(manifest)
    return manifest


def load_manifest_for_release(manifest_path: Path, expected_sha256: str) -> PipelineAssetManifest:
    """Strict load then assert self-hash == expected_sha256 (PIPELINE_ASSET_MISMATCH)."""
    manifest = load_pipeline_asset_manifest(manifest_path)
    if manifest.asset_manifest_sha256 != expected_sha256:
        raise _asset_error("asset manifest self-hash does not match the expected release hash.")
    return manifest


def verify_assets(
    manifest: PipelineAssetManifest,
    asset_paths: dict[str, Path],
) -> None:
    """Verify every manifest asset by exact file bytes; fail closed."""
    _validate_manifest(manifest)
    for logical_name, expected_sha in manifest.assets.items():
        if logical_name not in asset_paths:
            raise _asset_error(f"no path mapping for asset '{logical_name}'.")
        path = Path(asset_paths[logical_name])
        if not path.exists():
            raise _asset_error(f"asset path for '{logical_name}' does not exist.")
        if not path.is_file():
            raise _asset_error(f"asset path for '{logical_name}' is not a regular file.")
        try:
            actual_sha = compute_file_sha256(path)
        except OSError as exc:
            raise _asset_error(f"asset '{logical_name}' could not be read.") from exc
        if actual_sha != expected_sha:
            raise _asset_error(f"asset '{logical_name}' SHA256 does not match the manifest.")


def verify_remote_runtime_commit(repo_root: Path, required: str) -> None:
    """Read-only ``git rev-parse HEAD`` verification; never mutates the repo."""
    argv = ["git", "-C", str(repo_root), "rev-parse", "HEAD"]
    try:
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlatformError("REMOTE_IMPLEMENTATION_MISMATCH", "git runtime check failed.") from exc
    if result.returncode != 0:
        raise PlatformError("REMOTE_IMPLEMENTATION_MISMATCH", "git runtime check exited nonzero.")
    observed = result.stdout.strip()
    if len(observed) != 40 or observed != required:
        raise PlatformError(
            "REMOTE_IMPLEMENTATION_MISMATCH",
            "Deployed runtime commit does not match the required commit.",
        )


def verify_asset_manifest(
    manifest_path: Path,
    asset_paths: dict[str, Path],
    repo_root: Path,
    required_runtime_commit: str,
) -> PipelineAssetManifest:
    """Composite verifier: strict load -> self-hash -> runtime commit -> assets."""
    manifest = load_pipeline_asset_manifest(manifest_path)
    verify_remote_runtime_commit(repo_root, required_runtime_commit)
    verify_assets(manifest, asset_paths)
    return manifest
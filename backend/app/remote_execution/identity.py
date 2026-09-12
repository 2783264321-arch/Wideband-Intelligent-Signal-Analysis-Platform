"""Local identity resolution for remote request/provenance freeze.

The control plane resolves recording identity (fingerprint + source hash),
local orchestrator commit, and asset-manifest hash BEFORE the pure request
builder consumes them. No second fingerprint/hash scheme is introduced; this
reuses ``build_recording_fingerprint``, ``resolve_source_data_sha256``,
``manifest_recording_for``, and the strict ``load_pipeline_asset_manifest``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import PlatformError
from app.ground_truth.model import GroundTruthModel
from app.imported_runs.fingerprint import build_recording_fingerprint, manifest_recording_for
from app.recordings.model import RecordingModel
from app.remote_execution.source_hash import resolve_source_data_sha256

_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class RemoteRecordingIdentity:
    recording_fingerprint: str
    source_data_sha256: str
    dataset_name: str
    dataset_split: str
    dataset_key: str
    label_space: str
    local_run_id: str


def resolve_remote_recording_identity(
    session: Session,
    recording: RecordingModel,
    data_root: Path,
    local_run_id: str,
) -> RemoteRecordingIdentity:
    """Resolve the double identity + SpaceNet logical identity for one Recording.

    ``source_data_sha256`` is the exact raw-IQ byte hash;
    ``recording_fingerprint`` is the semantic identity built from the Recording
    metadata + GroundTruth via ``build_recording_fingerprint``.

    The GroundTruth SELECT is intentionally performed BEFORE
    ``resolve_source_data_sha256`` stages the source-hash cache: once the cache is
    a dirty ORM value, any later query could autoflush it, and ``prepare_run`` has
    deliberately chosen not to flush. The GroundTruth query does not depend on
    the source hash, so this reorder changes no scientific/provenance semantics.
    """
    gt_rows = list(
        session.scalars(
            select(GroundTruthModel).where(GroundTruthModel.recording_id == recording.id)
        ).all()
    )
    source = resolve_source_data_sha256(session, recording, data_root)
    manifest_recording = manifest_recording_for(recording, gt_rows)
    fingerprint = build_recording_fingerprint(
        recording.dataset_name,
        recording.dataset_split,
        recording.label_space,
        manifest_recording,
    ).sha256
    return RemoteRecordingIdentity(
        recording_fingerprint=fingerprint,
        source_data_sha256=source,
        dataset_name=recording.dataset_name,
        dataset_split=recording.dataset_split,
        dataset_key=recording.name,
        label_space=recording.label_space,
        local_run_id=local_run_id,
    )


def resolve_local_orchestrator_commit(project_root: Path) -> str:
    """Read-only local orchestrator commit via ``git rev-parse HEAD``.

    Validates exactly 40 lowercase hex; never mutates the Git working tree.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlatformError(
            "ORCHESTRATOR_COMMIT_UNAVAILABLE", "Unable to resolve local orchestrator commit."
        ) from exc
    if result.returncode != 0:
        raise PlatformError(
            "ORCHESTRATOR_COMMIT_UNAVAILABLE", "git rev-parse HEAD exited nonzero."
        )
    commit = result.stdout.strip()
    if _GIT_COMMIT_RE.fullmatch(commit) is None:
        raise PlatformError(
            "ORCHESTRATOR_COMMIT_UNAVAILABLE", "Local orchestrator commit is not a valid 40-hex SHA."
        )
    return commit


def resolve_asset_manifest_sha256(store, plugin_id: str, plugin_version: str, requested: str | None = None) -> str:
    """Resolve a ModelRelease through the store and return its verified manifest self-hash.

    Delegates to ``ModelReleaseStore.resolve`` (explicit requested release or the
    platform default), which fully verifies the referenced AssetManifest identity
    and self-hash. Never a reverse/index lookup.
    """
    return store.resolve(plugin_id, plugin_version, requested).manifest.asset_manifest_sha256
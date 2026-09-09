"""Remote worker deployment context (server side, Task 12F-B).

Constructed on the remote GPU server from exact fixed scalar environment
variable names only. It carries NO SSH/credential reference and no
request-controlled path. Parsing is pure (no filesystem/network I/O) and
fail-closes (``PlatformError("REMOTE_WORKER_CONTEXT_INVALID")``) on any missing
or unsafe scalar field.

Repo-owned paths (label-space root, asset-manifest path) are derived from the
validated ``repo_root`` and need no extra deployment configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from app.core.errors import PlatformError
from app.remote_execution.profile import is_safe_remote_posix_path_text

ENV_REPO_ROOT = "WSP_REMOTE_REPO_ROOT"
ENV_JOB_ROOT = "WSP_REMOTE_JOB_ROOT"
ENV_REQUIRED_RUNTIME_COMMIT = "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT"
ENV_SPACENET_ROOT = "WSP_REMOTE_SPACENET_ROOT"
ENV_DETECTOR_CHECKPOINT = "WSP_REMOTE_DETECTOR_CHECKPOINT"
ENV_FRN_CHECKPOINT = "WSP_REMOTE_FRN_CHECKPOINT"
ENV_FROZEN_CONFIG = "WSP_REMOTE_FROZEN_CONFIG"
ENV_LS_STFT_NORMALIZATION = "WSP_REMOTE_LS_STFT_NORMALIZATION"

_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def required_worker_env_vars() -> tuple[str, ...]:
    """Exact fixed scalar environment variable names of the worker contract."""
    return (
        ENV_REPO_ROOT,
        ENV_JOB_ROOT,
        ENV_REQUIRED_RUNTIME_COMMIT,
        ENV_SPACENET_ROOT,
        ENV_DETECTOR_CHECKPOINT,
        ENV_FRN_CHECKPOINT,
        ENV_FROZEN_CONFIG,
        ENV_LS_STFT_NORMALIZATION,
    )


def is_complete_worker_env(env) -> bool:
    """True iff every required worker scalar env name is present/non-empty."""
    for name in required_worker_env_vars():
        if not env.get(name):
            return False
    return True


@dataclass(frozen=True)
class RemoteWorkerContext:
    repo_root: Path
    job_root: Path
    required_runtime_commit: str
    dataset_root_space_net: Path
    detector_checkpoint: Path
    frn_checkpoint: Path
    frozen_config_path: Path
    ls_stft_normalization_path: Path
    label_space_root: Path
    asset_manifest_path: Path

    @classmethod
    def from_env(cls, env=None) -> "RemoteWorkerContext":
        """Parse the fixed scalar worker environment, failing closed on any
        missing/unsafe scalar field. Performs zero filesystem/network I/O."""
        if env is None:
            env = os.environ

        def _require_posix(name: str) -> str:
            value = env.get(name, "")
            if not value:
                raise PlatformError(
                    "REMOTE_WORKER_CONTEXT_INVALID",
                    f"{name} must be configured.",
                )
            if not is_safe_remote_posix_path_text(value):
                raise PlatformError(
                    "REMOTE_WORKER_CONTEXT_INVALID",
                    f"{name} is not a safe absolute POSIX path.",
                )
            return value

        repo_root_text = _require_posix(ENV_REPO_ROOT)
        job_root_text = _require_posix(ENV_JOB_ROOT)

        commit = env.get(ENV_REQUIRED_RUNTIME_COMMIT, "")
        if _GIT_COMMIT_RE.fullmatch(commit) is None:
            raise PlatformError(
                "REMOTE_WORKER_CONTEXT_INVALID",
                f"{ENV_REQUIRED_RUNTIME_COMMIT} must be a 40-hex commit.",
            )

        repo_root = Path(repo_root_text)
        return cls(
            repo_root=repo_root,
            job_root=Path(job_root_text),
            required_runtime_commit=commit,
            dataset_root_space_net=Path(_require_posix(ENV_SPACENET_ROOT)),
            detector_checkpoint=Path(_require_posix(ENV_DETECTOR_CHECKPOINT)),
            frn_checkpoint=Path(_require_posix(ENV_FRN_CHECKPOINT)),
            frozen_config_path=Path(_require_posix(ENV_FROZEN_CONFIG)),
            ls_stft_normalization_path=Path(_require_posix(ENV_LS_STFT_NORMALIZATION)),
            label_space_root=repo_root / "label_spaces",
            asset_manifest_path=(
                repo_root / "backend" / "app" / "pipelines"
                / "zoomspec_yolo26n_aug_combined_frn_v3" / "asset_manifest.json"
            ),
        )
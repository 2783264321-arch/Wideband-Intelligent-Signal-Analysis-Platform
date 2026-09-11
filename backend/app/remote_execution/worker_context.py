"""Remote worker deployment context (server side).

Constructed on the remote GPU server from environment variables only. It carries
NO SSH/credential reference and no request-controlled path. Parsing is pure (no
filesystem/network I/O) and fail-closes
(``PlatformError("REMOTE_WORKER_CONTEXT_INVALID")``) on any missing or unsafe
field.

D4 is additive: the legacy ZoomSpec scalar env vars/fields are preserved so the
existing ``ZoomSpecRemoteItemExecutor`` path keeps working until D3B. Optional
generic configuration (``WSP_REMOTE_MANIFEST_ROOT`` + a namespaced
``WSP_REMOTE_ASSET_PATHS_JSON``) is parsed in addition. Generic resolution never
reads the legacy ZoomSpec fields and fails closed when its own configuration is
absent/invalid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Mapping

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

# Generic (D4) configuration — additive; never replaces the legacy fields.
ENV_MANIFEST_ROOT = "WSP_REMOTE_MANIFEST_ROOT"
ENV_ASSET_PATHS_JSON = "WSP_REMOTE_ASSET_PATHS_JSON"
ENV_DEVICE_TYPE = "WSP_REMOTE_DEVICE_TYPE"
ENV_DEVICE_INDEX = "WSP_REMOTE_DEVICE_INDEX"
ENV_PRECISION = "WSP_REMOTE_PRECISION"
ENV_ENVIRONMENT_REF = "WSP_REMOTE_ENVIRONMENT_REF"
ENV_ENVIRONMENT_LABEL = "WSP_REMOTE_ENVIRONMENT_LABEL"

_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DEVICE_TYPE_RE = re.compile(r"^(cpu|cuda)$")
_PRECISION_RE = re.compile(r"^(float32|float16)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOGICAL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LEGACY_ASSET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}$")


def _invalid(message: str) -> PlatformError:
    return PlatformError("REMOTE_WORKER_CONTEXT_INVALID", message)


def required_worker_env_vars() -> tuple[str, ...]:
    """Exact fixed scalar environment variable names of the legacy worker contract."""
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
    """True iff every required legacy worker scalar env name is present/non-empty."""
    for name in required_worker_env_vars():
        if not env.get(name):
            return False
    return True


def _parse_namespaced_assets(raw: str | None) -> dict[str, dict[str, Path]]:
    """Parse the optional generic namespaced asset mapping.

    Shape: ``{"<plugin_id>/<plugin_version>/<asset_manifest_sha256>": {"<logical>": "/abs/path"}}``.
    Absent/empty => {} (generic seam unavailable). Invalid => fail closed.
    """
    if raw is None or raw == "":
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        raise _invalid(f"{ENV_ASSET_PATHS_JSON} is not valid JSON.")
    if not isinstance(parsed, dict):
        raise _invalid(f"{ENV_ASSET_PATHS_JSON} must be a JSON object.")
    result: dict[str, dict[str, Path]] = {}
    for namespace, mapping in parsed.items():
        if not isinstance(namespace, str):
            raise _invalid(f"{ENV_ASSET_PATHS_JSON} keys must be strings.")
        if isinstance(mapping, str):
            # Legacy flat subset (``logical -> "/abs"``): delivered to this process
            # via the fixed scalar envs, so it is ignored here. A string value under
            # a non-logical key is ambiguous and fails closed.
            if _LEGACY_ASSET_NAME_RE.fullmatch(namespace) is None:
                raise _invalid(f"{ENV_ASSET_PATHS_JSON} has an ambiguous flat entry '{namespace}'.")
            continue
        parts = namespace.split("/")
        if len(parts) != 3 or not parts[0] or not parts[1] or _SHA256_RE.fullmatch(parts[2]) is None:
            raise _invalid(
                f"{ENV_ASSET_PATHS_JSON} key must be '<plugin_id>/<plugin_version>/<sha256>'."
            )
        if not isinstance(mapping, dict) or not mapping:
            raise _invalid(f"{ENV_ASSET_PATHS_JSON}[{namespace}] must be a non-empty object.")
        logical: dict[str, Path] = {}
        for name, path_text in mapping.items():
            if not isinstance(name, str) or _LOGICAL_NAME_RE.fullmatch(name) is None:
                raise _invalid(f"{ENV_ASSET_PATHS_JSON}[{namespace}] has an invalid logical name.")
            if not isinstance(path_text, str) or not is_safe_remote_posix_path_text(path_text):
                raise _invalid(
                    f"{ENV_ASSET_PATHS_JSON}[{namespace}][{name}] is not a safe absolute POSIX path."
                )
            logical[name] = Path(path_text)
        result[namespace] = logical
    return result


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

    # D4 generic (additive, optional).
    manifest_root: Path | None = None
    asset_paths: Mapping[str, Mapping[str, Path]] = field(default_factory=dict)
    device_type: str = "cuda"
    device_index: int = 0
    precision: str = "float16"
    environment_ref: str | None = None
    environment_label: str | None = None

    @classmethod
    def from_env(cls, env=None) -> "RemoteWorkerContext":
        """Parse the fixed scalar worker environment, failing closed on any
        missing/unsafe scalar field. Performs zero filesystem/network I/O."""
        if env is None:
            env = os.environ

        def _require_posix(name: str) -> str:
            value = env.get(name, "")
            if not value:
                raise _invalid(f"{name} must be configured.")
            if not is_safe_remote_posix_path_text(value):
                raise _invalid(f"{name} is not a safe absolute POSIX path.")
            return value

        repo_root_text = _require_posix(ENV_REPO_ROOT)
        job_root_text = _require_posix(ENV_JOB_ROOT)

        commit = env.get(ENV_REQUIRED_RUNTIME_COMMIT, "")
        if _GIT_COMMIT_RE.fullmatch(commit) is None:
            raise _invalid(f"{ENV_REQUIRED_RUNTIME_COMMIT} must be a 40-hex commit.")

        manifest_root_text = env.get(ENV_MANIFEST_ROOT, "")
        manifest_root: Path | None = None
        if manifest_root_text:
            if not is_safe_remote_posix_path_text(manifest_root_text):
                raise _invalid(f"{ENV_MANIFEST_ROOT} is not a safe absolute POSIX path.")
            manifest_root = Path(manifest_root_text)

        device_type = env.get(ENV_DEVICE_TYPE, "cuda")
        if device_type != "cuda":
            raise _invalid(f"remote_gpu is CUDA-only; {ENV_DEVICE_TYPE} must be 'cuda'.")
        device_index_raw = env.get(ENV_DEVICE_INDEX, "0")
        try:
            device_index = int(device_index_raw)
        except (TypeError, ValueError):
            raise _invalid(f"{ENV_DEVICE_INDEX} must be an integer.")
        if device_index < 0:
            raise _invalid(f"{ENV_DEVICE_INDEX} must be non-negative.")
        precision = env.get(ENV_PRECISION, "float16")
        if _PRECISION_RE.fullmatch(precision) is None:
            raise _invalid(f"{ENV_PRECISION} must be 'float32' or 'float16'.")

        def _optional_label(name: str) -> str | None:
            value = env.get(name, "")
            if not value:
                return None
            if _LABEL_RE.fullmatch(value) is None:
                raise _invalid(f"{name} is not a safe label.")
            return value

        environment_ref = _optional_label(ENV_ENVIRONMENT_REF)
        environment_label = _optional_label(ENV_ENVIRONMENT_LABEL)

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
            manifest_root=manifest_root,
            asset_paths=_parse_namespaced_assets(env.get(ENV_ASSET_PATHS_JSON)),
            device_type=device_type,
            device_index=device_index,
            precision=precision,
            environment_ref=environment_ref,
            environment_label=environment_label,
        )

    def runtime_descriptor(self):
        """Deployment-owned descriptor for this remote worker (probe/runtime).

        remote_gpu is CUDA-only in M9.2; a non-cuda or index-less descriptor fails
        closed (never substitutes device 0). The environment identity is carried so
        the reconstructed descriptor matches the control-plane profile descriptor.
        """
        from app.remote_execution.runtime import RuntimeDescriptor

        if self.device_type != "cuda":
            raise _invalid("remote_gpu is CUDA-only; non-cuda worker descriptors are unsupported.")
        if self.device_index is None or self.device_index < 0:
            raise _invalid("Remote CUDA device_index must be a configured non-negative integer.")
        return RuntimeDescriptor(
            executor="remote_gpu",
            device_type="cuda",
            device_index=self.device_index,
            precision=self.precision,
            environment_ref=self.environment_ref,
            environment_label=self.environment_label,
        )

    def resolve_assets(
        self,
        *,
        plugin_id: str,
        plugin_version: str,
        asset_manifest_sha256: str,
        manifest,
    ) -> dict[str, Path]:
        """Resolve manifest-authorized logical assets from the generic mapping.

        Fails closed when generic config is absent/invalid or a declared asset is
        missing. Never reads the legacy ZoomSpec asset fields.
        """
        if not self.asset_paths:
            raise _invalid("Generic remote asset mapping is not configured.")
        namespace = f"{plugin_id}/{plugin_version}/{asset_manifest_sha256}"
        namespaced = self.asset_paths.get(namespace)
        if not namespaced:
            raise _invalid(f"No generic asset mapping for namespace '{namespace}'.")
        assets: dict[str, Path] = {}
        for name in manifest.assets:
            if name not in namespaced:
                raise _invalid(f"Generic asset mapping is missing declared asset '{name}'.")
            assets[name] = namespaced[name]
        return assets

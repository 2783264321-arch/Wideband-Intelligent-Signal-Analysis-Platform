"""Remote executor profile: validated configuration only, never secrets.

The profile carries only local paths to credentials (never credential content)
and validated POSIX roots/mappings derived from the configured environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re

from app.core.config import Settings
from app.core.errors import PlatformError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9._:\-]+$")
_USER_RE = re.compile(r"^[A-Za-z0-9._\-]+$")
_REMOTE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def is_safe_remote_posix_path_text(value: str) -> bool:
    """Conservative raw remote-path check used by BOTH config and transport.

    Validated on the raw text BEFORE any PurePosixPath normalization so that
    ``.`` and duplicate separators cannot be hidden. Every component after the
    leading ``/`` must match ``[A-Za-z0-9._-]+`` and be non-empty / non-``.`` /
    non-``..``; shell-significant characters are therefore never allowed.
    """
    if not value.startswith("/"):
        return False
    if value != value.strip():
        return False
    if "\\" in value:
        return False
    if "\x00" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    raw_parts = value.split("/")
    if raw_parts[0] != "":
        return False
    if len(raw_parts) < 2:
        return False
    for part in raw_parts[1:]:
        if not part or part in (".", ".."):
            return False
        if _REMOTE_COMPONENT_RE.fullmatch(part) is None:
            return False
    return True


def _unavailable(message: str) -> PlatformError:
    return PlatformError("REMOTE_EXECUTOR_UNAVAILABLE", message)


def _safe_identifier(value: str, name: str) -> str:
    if not value or _IDENTIFIER_RE.fullmatch(value) is None:
        raise _unavailable(f"{name} must be a safe identifier.")
    return value


def _require_runtime_commit(value: str) -> str:
    if not value or _GIT_COMMIT_RE.fullmatch(value) is None:
        raise _unavailable("WSP_REMOTE_REQUIRED_RUNTIME_COMMIT must be a 40-hex commit.")
    return value


def _safe_host(value: str) -> str:
    if not value or _HOST_RE.fullmatch(value) is None:
        raise _unavailable("WSP_REMOTE_HOST contains unsafe characters.")
    return value


def _safe_user(value: str) -> str:
    if not value or _USER_RE.fullmatch(value) is None:
        raise _unavailable("WSP_REMOTE_USER contains unsafe characters.")
    return value


def _safe_local_path(value: str, name: str) -> Path:
    if not value:
        raise _unavailable(f"{name} must be configured.")
    path = Path(value)
    if not path.is_absolute():
        raise _unavailable(f"{name} must be an absolute path.")
    if not path.exists() or not path.is_file():
        raise _unavailable(f"{name} must be an existing regular file.")
    return path


def _safe_posix_root(value: str, name: str) -> PurePosixPath:
    if not value:
        raise _unavailable(f"{name} must be configured.")
    if not is_safe_remote_posix_path_text(value):
        raise _unavailable(f"{name} is not a safe absolute POSIX path.")
    return PurePosixPath(value)


def _safe_posix_mapping(value: str | None, name: str) -> dict[str, PurePosixPath]:
    if value is None or value == "":
        return {}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        raise _unavailable(f"{name} is not valid JSON.")
    if not isinstance(parsed, dict):
        raise _unavailable(f"{name} must be a JSON object.")
    result = {}
    for key, raw in parsed.items():
        _safe_identifier(key, f"{name} key")
        if not isinstance(raw, str):
            raise _unavailable(f"{name} values must be strings.")
        result[key] = _safe_posix_root(raw, f"{name}[{key}]")
    return result


def _parse_asset_paths(raw: str | None) -> tuple[dict[str, PurePosixPath], dict[str, dict[str, PurePosixPath]]]:
    """Partition ``WSP_REMOTE_ASSET_PATHS_JSON`` into legacy + generic subsets.

    A single top-level object MAY contain both: legacy ``logical -> "/abs/path"``
    string entries and generic ``"<plugin>/<version>/<sha>" -> {logical: "/abs"}``
    object entries. Entries are partitioned deterministically; malformed or
    ambiguous entries fail closed.
    """
    if raw is None or raw == "":
        return {}, {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        raise _unavailable("WSP_REMOTE_ASSET_PATHS_JSON is not valid JSON.")
    if not isinstance(parsed, dict):
        raise _unavailable("WSP_REMOTE_ASSET_PATHS_JSON must be a JSON object.")
    flat: dict[str, PurePosixPath] = {}
    generic: dict[str, dict[str, PurePosixPath]] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key:
            raise _unavailable("WSP_REMOTE_ASSET_PATHS_JSON keys must be non-empty strings.")
        if isinstance(value, str):
            _safe_identifier(key, "WSP_REMOTE_ASSET_PATHS_JSON legacy key")
            flat[key] = _safe_posix_root(value, f"WSP_REMOTE_ASSET_PATHS_JSON[{key}]")
        elif isinstance(value, dict):
            parts = key.split("/")
            if len(parts) != 3 or not parts[0] or not parts[1] or _SHA256_RE.fullmatch(parts[2]) is None:
                raise _unavailable(
                    "WSP_REMOTE_ASSET_PATHS_JSON generic key must be "
                    "'<plugin_id>/<plugin_version>/<sha256>'."
                )
            if not value:
                raise _unavailable(f"WSP_REMOTE_ASSET_PATHS_JSON[{key}] must be a non-empty object.")
            logical: dict[str, PurePosixPath] = {}
            for logical_name, path_text in value.items():
                if not isinstance(logical_name, str) or _IDENTIFIER_RE.fullmatch(logical_name) is None:
                    raise _unavailable(f"WSP_REMOTE_ASSET_PATHS_JSON[{key}] has an invalid logical name.")
                if not isinstance(path_text, str):
                    raise _unavailable(f"WSP_REMOTE_ASSET_PATHS_JSON[{key}][{logical_name}] must be a string.")
                logical[logical_name] = _safe_posix_root(
                    path_text, f"WSP_REMOTE_ASSET_PATHS_JSON[{key}][{logical_name}]"
                )
            generic[key] = logical
        else:
            raise _unavailable(
                "WSP_REMOTE_ASSET_PATHS_JSON entries must be a string (legacy) or object (generic)."
            )
    return flat, generic


@dataclass(frozen=True)
class RemoteProfile:
    name: str
    host: str
    port: int
    user: str

    ssh_key_path: Path
    known_hosts_path: Path

    remote_repo_root: PurePosixPath
    remote_job_root: PurePosixPath
    remote_python_path: PurePosixPath
    required_remote_runtime_commit: str

    dataset_roots: dict[str, PurePosixPath]
    asset_paths: dict[str, PurePosixPath]

    # D4 generic (additive, optional).
    manifest_root: PurePosixPath | None = None
    generic_asset_paths: dict[str, dict[str, PurePosixPath]] = field(default_factory=dict)
    device_type: str = "cuda"
    device_index: int = 0
    precision: str = "float16"

    def runtime_descriptor(self):
        """The deployment-owned RuntimeDescriptor for this remote profile.

        Shared by the control-plane ``RemoteGpuExecutorProvider`` and (via env)
        the remote worker/probe. Never derived from request/wire/plugin data.
        """
        from app.remote_execution.runtime import RuntimeDescriptor

        if self.device_type != "cuda":
            raise _unavailable("remote_gpu is CUDA-only; non-cuda profiles are unsupported.")
        if self.device_index is None or self.device_index < 0:
            raise _unavailable("Remote CUDA device_index must be a configured non-negative integer.")
        return RuntimeDescriptor(
            executor="remote_gpu",
            device_type="cuda",
            device_index=self.device_index,
            precision=self.precision,
            environment_ref=self.name,
            environment_label=self.name,
        )

    @classmethod
    def from_env(cls, settings: Settings) -> "RemoteProfile":
        del settings  # signature kept for config lifecycle consistency; env is authoritative
        env = os.environ

        def _get(name: str) -> str:
            value = env.get(name, "")
            if not value:
                raise _unavailable(f"{name} must be configured.")
            return value

        name = _safe_identifier(_get("WSP_REMOTE_PROFILE_NAME"), "WSP_REMOTE_PROFILE_NAME")
        host = _safe_host(_get("WSP_REMOTE_HOST"))
        user = _safe_user(_get("WSP_REMOTE_USER"))

        port_raw = _get("WSP_REMOTE_PORT")
        try:
            port = int(port_raw)
        except ValueError:
            raise _unavailable("WSP_REMOTE_PORT must be an integer.")
        if not (1 <= port <= 65535):
            raise _unavailable("WSP_REMOTE_PORT must be within 1..65535.")

        ssh_key_path = _safe_local_path(_get("WSP_REMOTE_SSH_KEY_PATH"), "WSP_REMOTE_SSH_KEY_PATH")
        known_hosts_path = _safe_local_path(_get("WSP_REMOTE_KNOWN_HOSTS_PATH"), "WSP_REMOTE_KNOWN_HOSTS_PATH")

        remote_repo_root = _safe_posix_root(_get("WSP_REMOTE_REPO_ROOT"), "WSP_REMOTE_REPO_ROOT")
        remote_job_root = _safe_posix_root(_get("WSP_REMOTE_JOB_ROOT"), "WSP_REMOTE_JOB_ROOT")
        # The remote Python executable is immutable runtime configuration. It is
        # validated as a safe absolute POSIX path only; the local computer cannot
        # inspect the server filesystem, so no Path.exists() is used here.
        remote_python_path = _safe_posix_root(_get("WSP_REMOTE_PYTHON_PATH"), "WSP_REMOTE_PYTHON_PATH")
        required_remote_runtime_commit = _require_runtime_commit(_get("WSP_REMOTE_REQUIRED_RUNTIME_COMMIT"))

        dataset_roots = _safe_posix_mapping(env.get("WSP_REMOTE_DATASET_ROOTS_JSON"), "WSP_REMOTE_DATASET_ROOTS_JSON")
        asset_paths, generic_asset_paths = _parse_asset_paths(env.get("WSP_REMOTE_ASSET_PATHS_JSON"))

        manifest_root_text = env.get("WSP_REMOTE_MANIFEST_ROOT", "")
        manifest_root = (
            _safe_posix_root(manifest_root_text, "WSP_REMOTE_MANIFEST_ROOT")
            if manifest_root_text
            else None
        )
        device_type = env.get("WSP_REMOTE_DEVICE_TYPE", "cuda")
        if device_type != "cuda":
            raise _unavailable("remote_gpu is CUDA-only; WSP_REMOTE_DEVICE_TYPE must be 'cuda'.")
        try:
            device_index = int(env.get("WSP_REMOTE_DEVICE_INDEX", "0"))
        except (TypeError, ValueError):
            raise _unavailable("WSP_REMOTE_DEVICE_INDEX must be an integer.")
        if device_index < 0:
            raise _unavailable("WSP_REMOTE_DEVICE_INDEX must be non-negative.")
        precision = env.get("WSP_REMOTE_PRECISION", "float16")
        if precision not in ("float32", "float16"):
            raise _unavailable("WSP_REMOTE_PRECISION must be 'float32' or 'float16'.")

        return cls(
            name=name,
            host=host,
            port=port,
            user=user,
            ssh_key_path=ssh_key_path,
            known_hosts_path=known_hosts_path,
            remote_repo_root=remote_repo_root,
            remote_job_root=remote_job_root,
            remote_python_path=remote_python_path,
            required_remote_runtime_commit=required_remote_runtime_commit,
            dataset_roots=dataset_roots,
            asset_paths=asset_paths,
            manifest_root=manifest_root,
            generic_asset_paths=generic_asset_paths,
            device_type=device_type,
            device_index=device_index,
            precision=precision,
        )
"""Remote executor profile: validated configuration only, never secrets.

The profile carries only local paths to credentials (never credential content)
and validated POSIX roots/mappings derived from the configured environment.
"""
from __future__ import annotations

from dataclasses import dataclass
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

    dataset_roots: dict[str, PurePosixPath]
    asset_paths: dict[str, PurePosixPath]

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

        dataset_roots = _safe_posix_mapping(env.get("WSP_REMOTE_DATASET_ROOTS_JSON"), "WSP_REMOTE_DATASET_ROOTS_JSON")
        asset_paths = _safe_posix_mapping(env.get("WSP_REMOTE_ASSET_PATHS_JSON"), "WSP_REMOTE_ASSET_PATHS_JSON")

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
            dataset_roots=dataset_roots,
            asset_paths=asset_paths,
        )
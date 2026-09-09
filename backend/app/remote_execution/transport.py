"""Fixed-argv OpenSSH/SCP transport.

Every subprocess uses ``shell=False`` with host-key verification enabled
(``StrictHostKeyChecking=yes`` plus an explicit known-hosts file). Only the
fixed platform-owned runner entrypoint and strict validated identifiers /
trusted absolute POSIX paths may appear in SSH/SCP argv.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import subprocess

from app.core.errors import PlatformError
from app.remote_execution.profile import RemoteProfile, is_safe_remote_posix_path_text
from app.remote_execution.worker_context import (
    ENV_DETECTOR_CHECKPOINT,
    ENV_FRN_CHECKPOINT,
    ENV_FROZEN_CONFIG,
    ENV_JOB_ROOT,
    ENV_LS_STFT_NORMALIZATION,
    ENV_REPO_ROOT,
    ENV_REQUIRED_RUNTIME_COMMIT,
    ENV_SPACENET_ROOT,
)

_RUNNER_COMMANDS = ("probe", "submit", "status", "work")
_FULL_WORKER_ENV_SUBCOMMANDS = ("probe", "submit", "work")
_REQUIRED_DATASET_LOGICAL_KEYS = ("SpaceNet",)
_REQUIRED_ASSET_LOGICAL_KEYS = (
    "detector_checkpoint",
    "frn_checkpoint",
    "frozen_config",
    "ls_stft_normalization",
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_FLAG_RE = re.compile(r"^--[A-Za-z0-9_-]+$")
_RUNNER_ERROR_RE = re.compile(r"^([A-Z][A-Z0-9_]{1,63}): (.+)$")
_MAX_RUNNER_MESSAGE_LEN = 500


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_runner_error_line(stderr: str | bytes | None) -> tuple[str, str] | None:
    """Return (code, message) iff stderr is exactly one bounded runner-error
    line 'CODE: message'. Arbitrary SSH/traceback stderr returns None."""
    line = _coerce_text(stderr).strip()
    if not line or "\n" in line or "\r" in line:
        return None
    match = _RUNNER_ERROR_RE.fullmatch(line)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _sanitize_runner_message(message: str | bytes | None) -> str:
    """Bounded, control-char-stripped message. Never includes tracebacks/paths."""
    if message is None:
        return ""
    cleaned = "".join(ch for ch in _coerce_text(message) if ch.isprintable() or ch in "\t")
    return cleaned[:_MAX_RUNNER_MESSAGE_LEN]


class RemoteTransportError(RuntimeError):
    """Remote transport command failed (SSH/SCP-level). No server stderr leaked."""
    pass


class RemoteRunnerExit(RemoteTransportError):
    """A runner subprocess returned nonzero with a single controlled stderr
    line 'CODE: message'. Only a validated uppercase error code + bounded
    sanitized message are retained; arbitrary stderr is never exposed."""

    def __init__(self, returncode: int, code: str | None, message: str | None, stdout: str):
        super().__init__(f"Remote runner exited with code {returncode}.")
        self.returncode = returncode
        self.code = code
        self.message = message
        self.stdout = stdout


def _is_safe_remote_posix_path(value: str) -> bool:
    return is_safe_remote_posix_path_text(value)


def _validate_remote_posix_path(path: str | PurePosixPath) -> PurePosixPath:
    text = path.as_posix() if isinstance(path, PurePosixPath) else path
    if not is_safe_remote_posix_path_text(text):
        raise PlatformError("REMOTE_TRANSPORT_ERROR", "Remote path is not a safe absolute POSIX path.")
    return PurePosixPath(text)


class SshRunner:
    def __init__(self, profile: RemoteProfile, run_process=subprocess.run):
        self.profile = profile
        self._run_process = run_process

    def _ssh_base_argv(self) -> list[str]:
        return [
            "ssh",
            "-p", str(self.profile.port),
            "-i", str(self.profile.ssh_key_path),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.profile.known_hosts_path}",
        ]

    def _scp_base_argv(self) -> list[str]:
        return [
            "scp",
            "-P", str(self.profile.port),
            "-i", str(self.profile.ssh_key_path),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.profile.known_hosts_path}",
        ]

    def _destination(self) -> str:
        return f"{self.profile.user}@{self.profile.host}"

    def _invoke(self, argv: list[str]) -> subprocess.CompletedProcess:
        result = self._run_process(argv, shell=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RemoteTransportError("Remote transport command exited nonzero.")
        return result

    def _invoke_runner(self, argv: list[str]) -> subprocess.CompletedProcess:
        result = self._run_process(argv, shell=False, capture_output=True, text=True)
        if result.returncode != 0:
            parsed = _parse_runner_error_line(result.stderr)
            if parsed is not None:
                code, message = parsed
                raise RemoteRunnerExit(result.returncode, code, _sanitize_runner_message(message), result.stdout)
            raise RemoteTransportError("Remote transport command exited nonzero.")
        return result

    def validate_runner_environment(self, subcommand: str) -> None:
        """Zero subprocess/network-I/O preflight that this subcommand's required
        runner env is satisfiable from the ``RemoteProfile``.

        probe/submit/work require the full worker env mappings (SpaceNet root +
        all four required asset scalars). status requires only the repo/job
        fields needed for the minimal status env.

        Raises ``RemoteTransportError`` (never raw ``PlatformError`` / never
        ``RemoteRunnerExit``) on missing/invalid worker mappings so existing
        consumers fail closed through their current transport-error boundaries.
        """
        if subcommand not in _RUNNER_COMMANDS:
            raise PlatformError(
                "REMOTE_TRANSPORT_ERROR", f"Runner subcommand '{subcommand}' is not allowed."
            )
        for name, value in (
            ("WSP_REMOTE_REPO_ROOT", self.profile.remote_repo_root),
            ("WSP_REMOTE_JOB_ROOT", self.profile.remote_job_root),
        ):
            if not is_safe_remote_posix_path_text(value.as_posix()):
                raise RemoteTransportError(
                    "Remote worker environment is not satisfiable from the configured profile."
                )
        if subcommand not in _FULL_WORKER_ENV_SUBCOMMANDS:
            return
        spacenet = self.profile.dataset_roots.get(_REQUIRED_DATASET_LOGICAL_KEYS[0])
        if spacenet is None or not is_safe_remote_posix_path_text(spacenet.as_posix()):
            raise RemoteTransportError(
                "Remote worker deployment is missing the SpaceNet dataset root mapping."
            )
        for logical_name in _REQUIRED_ASSET_LOGICAL_KEYS:
            path = self.profile.asset_paths.get(logical_name)
            if path is None or not is_safe_remote_posix_path_text(path.as_posix()):
                raise RemoteTransportError(
                    f"Remote worker deployment is missing required asset mapping '{logical_name}'."
                )

    def _runner_env_prefix(
        self,
        subcommand: str,
        module_root: PurePosixPath,
        job_root: PurePosixPath,
    ) -> list[str]:
        """Subcommand-specific scalar env bridge. probe/submit/work receive the
        full worker env; status receives minimal env only."""
        prefix = [
            f"PYTHONPATH={module_root.as_posix()}",
            f"{ENV_JOB_ROOT}={job_root.as_posix()}",
        ]
        if subcommand not in _FULL_WORKER_ENV_SUBCOMMANDS:
            return prefix
        repo_root = _validate_remote_posix_path(self.profile.remote_repo_root)
        prefix.extend(
            [
                f"{ENV_REPO_ROOT}={repo_root.as_posix()}",
                f"{ENV_REQUIRED_RUNTIME_COMMIT}={self.profile.required_remote_runtime_commit}",
                f"{ENV_SPACENET_ROOT}={self.profile.dataset_roots[_REQUIRED_DATASET_LOGICAL_KEYS[0]].as_posix()}",
                f"{ENV_DETECTOR_CHECKPOINT}={self.profile.asset_paths['detector_checkpoint'].as_posix()}",
                f"{ENV_FRN_CHECKPOINT}={self.profile.asset_paths['frn_checkpoint'].as_posix()}",
                f"{ENV_FROZEN_CONFIG}={self.profile.asset_paths['frozen_config'].as_posix()}",
                f"{ENV_LS_STFT_NORMALIZATION}={self.profile.asset_paths['ls_stft_normalization'].as_posix()}",
            ]
        )
        return prefix

    def run_runner(
        self,
        subcommand: str,
        args: list[str] | tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess:
        if subcommand not in _RUNNER_COMMANDS:
            raise PlatformError("REMOTE_TRANSPORT_ERROR", f"Runner subcommand '{subcommand}' is not allowed.")
        # Defensive internal preflight: a local missing/invalid worker mapping
        # fails closed BEFORE any SSH invocation.
        self.validate_runner_environment(subcommand)
        for argument in args:
            if not self._is_safe_runner_token(argument):
                raise PlatformError("REMOTE_TRANSPORT_ERROR", "Runner argument is not a safe token.")
        # Deterministic module root and runtime: PYTHONPATH is generated ONLY
        # from the conservative validated remote_repo_root; WSP_REMOTE_JOB_ROOT
        # and the remote Python executable come from the validated profile.
        # No request-controlled value ever reaches argv.
        module_root = _validate_remote_posix_path(self.profile.remote_repo_root / "backend")
        job_root = _validate_remote_posix_path(self.profile.remote_job_root)
        python_path = _validate_remote_posix_path(self.profile.remote_python_path)
        argv = [
            *self._ssh_base_argv(),
            self._destination(),
            "env",
            *self._runner_env_prefix(subcommand, module_root, job_root),
            python_path.as_posix(),
            "-m",
            "app.remote_execution.runner",
            subcommand,
            *args,
        ]
        return self._invoke_runner(argv)

    @staticmethod
    def _is_safe_runner_token(token: str) -> bool:
        if _FLAG_RE.fullmatch(token):
            return True
        if _IDENTIFIER_RE.fullmatch(token):
            return True
        return _is_safe_remote_posix_path(token)

    def upload_file(self, local_path: Path, remote_path: str | PurePosixPath) -> None:
        remote = _validate_remote_posix_path(remote_path)
        if not local_path.is_file():
            raise PlatformError("REMOTE_TRANSPORT_ERROR", "Upload source must be a regular file.")
        argv = [
            *self._scp_base_argv(),
            str(local_path),
            f"{self._destination()}:{remote.as_posix()}",
        ]
        self._invoke(argv)

    def download_file(self, remote_path: str | PurePosixPath, local_path: Path) -> None:
        remote = _validate_remote_posix_path(remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            *self._scp_base_argv(),
            f"{self._destination()}:{remote.as_posix()}",
            str(local_path),
        ]
        self._invoke(argv)
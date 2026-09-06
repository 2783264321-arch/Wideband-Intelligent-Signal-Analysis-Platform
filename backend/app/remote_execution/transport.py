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
from app.remote_execution.profile import RemoteProfile

_RUNNER_COMMANDS = ("probe", "submit", "status", "work")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_FLAG_RE = re.compile(r"^--[A-Za-z0-9_-]+$")


class RemoteTransportError(RuntimeError):
    pass


def _is_safe_remote_posix_path(value: str) -> bool:
    if not value:
        return False
    if "\x00" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    path = PurePosixPath(value)
    if not path.is_absolute():
        return False
    return bool(path.parts) and not any(part in ("", ".", "..") for part in path.parts)


def _validate_remote_posix_path(path: PurePosixPath) -> None:
    if not _is_safe_remote_posix_path(path.as_posix()):
        raise PlatformError("REMOTE_TRANSPORT_ERROR", "Remote path is not a safe absolute POSIX path.")


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

    def run_runner(
        self,
        subcommand: str,
        args: list[str] | tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess:
        if subcommand not in _RUNNER_COMMANDS:
            raise PlatformError("REMOTE_TRANSPORT_ERROR", f"Runner subcommand '{subcommand}' is not allowed.")
        for argument in args:
            if not self._is_safe_runner_token(argument):
                raise PlatformError("REMOTE_TRANSPORT_ERROR", "Runner argument is not a safe token.")
        argv = [
            *self._ssh_base_argv(),
            self._destination(),
            "python3",
            "-m",
            "app.remote_execution.runner",
            subcommand,
            *args,
        ]
        return self._invoke(argv)

    @staticmethod
    def _is_safe_runner_token(token: str) -> bool:
        if _FLAG_RE.fullmatch(token):
            return True
        if _IDENTIFIER_RE.fullmatch(token):
            return True
        return _is_safe_remote_posix_path(token)

    def upload_file(self, local_path: Path, remote_path: PurePosixPath) -> None:
        _validate_remote_posix_path(remote_path)
        if not local_path.is_file():
            raise PlatformError("REMOTE_TRANSPORT_ERROR", "Upload source must be a regular file.")
        argv = [
            *self._scp_base_argv(),
            str(local_path),
            f"{self._destination()}:{remote_path.as_posix()}",
        ]
        self._invoke(argv)

    def download_file(self, remote_path: PurePosixPath, local_path: Path) -> None:
        _validate_remote_posix_path(remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            *self._scp_base_argv(),
            f"{self._destination()}:{remote_path.as_posix()}",
            str(local_path),
        ]
        self._invoke(argv)
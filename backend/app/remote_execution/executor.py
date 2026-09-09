"""Injected dependency interface + local control-plane probe adapter for remote
executor availability.

``RemoteExecutorProbe`` is the protocol consumed by ``AnalysisService``. The
production ``SshRemoteExecutorProbe`` adapter reads ``RemoteProfile`` /
``SshRunner`` configuration, invokes the runner ``probe`` subcommand over the
existing SSH transport, and maps a deterministic probe success/error/return-code
into an :class:`~app.analysis.schema.ExecutorAvailabilityRead`. The remote
producer is implemented in 12F-B; 12F-A consumes a frozen fake response and
never touches real SSH/GPU.
"""
from __future__ import annotations

import json
from typing import Protocol

from app.analysis.schema import ExecutorAvailabilityRead
from app.pipelines.base import PipelineDefinition
from app.recordings.model import RecordingModel
from app.remote_execution.schema import RemoteProbeResponseV1
from app.remote_execution.transport import RemoteRunnerExit, RemoteTransportError


class RemoteExecutorProbe(Protocol):
    def availability(
        self,
        recording: RecordingModel,
        pipeline: PipelineDefinition,
        source_data_sha256: str | None,
    ) -> ExecutorAvailabilityRead:
        ...


def _parse_probe_response(stdout: str | bytes) -> RemoteProbeResponseV1:
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise ValueError("probe stdout must be a JSON object")
    return RemoteProbeResponseV1.model_validate(payload)


class SshRemoteExecutorProbe(RemoteExecutorProbe):
    """Local adapter that runs the remote runner ``probe`` over SSH and maps a
    deterministic transport response into an ``ExecutorAvailabilityRead``.

    Expected runtime/manifest identities are injected through the constructor so
    this adapter is independently testable with a fake transport and does not
    depend on the remote probe body (12F-B).
    """

    def __init__(
        self,
        profile,
        transport,
        *,
        expected_runtime_commit: str,
        expected_manifest_sha256: str,
    ) -> None:
        self._profile = profile
        self._transport = transport
        self._expected_runtime_commit = expected_runtime_commit
        self._expected_manifest_sha256 = expected_manifest_sha256

    def availability(
        self,
        recording: RecordingModel,
        pipeline: PipelineDefinition,
        source_data_sha256: str | None,
    ) -> ExecutorAvailabilityRead:
        del source_data_sha256  # probe identity is runtime/manifest-scoped; not per-recording
        try:
            result = self._transport.run_runner("probe", ())
        except RemoteRunnerExit as exc:
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=False,
                reason_code=exc.code or "REMOTE_EXECUTOR_UNAVAILABLE",
                reason_message=exc.message,
                remote_profile=self._profile.name,
                recommended=False,
            )
        except RemoteTransportError:
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=False,
                reason_code="REMOTE_TRANSPORT_UNAVAILABLE",
                reason_message="Remote transport is unreachable.",
                remote_profile=self._profile.name,
                recommended=False,
            )

        try:
            response = _parse_probe_response(result.stdout)
        except Exception:
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=False,
                reason_code="REMOTE_PROBE_UNAVAILABLE",
                reason_message="Remote probe returned an invalid response.",
                remote_profile=self._profile.name,
                recommended=False,
            )

        if response.remote_runtime_commit != self._expected_runtime_commit:
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=False,
                reason_code="REMOTE_IMPLEMENTATION_MISMATCH",
                reason_message="Remote runtime commit does not match the required remote runtime.",
                remote_profile=self._profile.name,
                recommended=False,
            )
        if response.asset_manifest_sha256 != self._expected_manifest_sha256:
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=False,
                reason_code="PIPELINE_ASSET_MISMATCH",
                reason_message="Remote asset manifest hash does not match the local manifest.",
                remote_profile=self._profile.name,
                recommended=False,
            )
        return ExecutorAvailabilityRead(
            executor="remote_gpu",
            available=True,
            reason_code=None,
            reason_message=None,
            remote_profile=self._profile.name,
            recommended=(pipeline.recommended_executor == "remote_gpu"),
        )
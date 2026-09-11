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
        model_release: object | None = None,
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

    The probe identity is per-call: the already-resolved ModelRelease supplies the
    exact plugin/version/release/manifest tokens. There is no global manifest.
    """

    def __init__(
        self,
        profile,
        transport,
        *,
        expected_runtime_commit: str,
    ) -> None:
        self._profile = profile
        self._transport = transport
        self._expected_runtime_commit = expected_runtime_commit

    def _unavailable(self, code: str, message: str) -> ExecutorAvailabilityRead:
        return ExecutorAvailabilityRead(
            executor="remote_gpu",
            available=False,
            reason_code=code,
            reason_message=message,
            remote_profile=self._profile.name,
            recommended=False,
        )

    def availability(
        self,
        recording: RecordingModel,
        pipeline: PipelineDefinition,
        source_data_sha256: str | None,
        model_release: object | None = None,
    ) -> ExecutorAvailabilityRead:
        del source_data_sha256  # probe identity is plugin/release/manifest-scoped
        if model_release is None:
            return self._unavailable(
                "MODEL_RELEASE_MISMATCH",
                "Remote execution requires a resolved model release.",
            )
        plugin_id = pipeline.plugin_id
        plugin_version = pipeline.plugin_version
        model_release_id = model_release.release.model_release_id
        asset_manifest_sha256 = model_release.manifest.asset_manifest_sha256
        args = (
            "--plugin-id", plugin_id,
            "--plugin-version", plugin_version,
            "--model-release-id", model_release_id,
            "--asset-manifest-sha256", asset_manifest_sha256,
        )
        try:
            result = self._transport.run_runner("probe", args)
        except RemoteRunnerExit as exc:
            return self._unavailable(
                exc.code or "REMOTE_EXECUTOR_UNAVAILABLE", exc.message
            )
        except RemoteTransportError:
            return self._unavailable(
                "REMOTE_TRANSPORT_UNAVAILABLE", "Remote transport is unreachable."
            )

        try:
            response = _parse_probe_response(result.stdout)
        except Exception:
            return self._unavailable(
                "REMOTE_PROBE_UNAVAILABLE", "Remote probe returned an invalid response."
            )

        if response.remote_runtime_commit != self._expected_runtime_commit:
            return self._unavailable(
                "REMOTE_IMPLEMENTATION_MISMATCH",
                "Remote runtime commit does not match the required remote runtime.",
            )
        if response.asset_manifest_sha256 != asset_manifest_sha256:
            return self._unavailable(
                "PIPELINE_ASSET_MISMATCH",
                "Remote asset manifest hash does not match the requested release manifest.",
            )
        # The live-probed device must equal the deployment-owned RuntimeDescriptor
        # device_index (never a fallback to 0 or another index).
        descriptor = self._profile.runtime_descriptor()
        if response.device != descriptor.device_index:
            return self._unavailable(
                "REMOTE_PROBE_UNAVAILABLE",
                "Remote probe device does not match the configured runtime descriptor.",
            )
        return ExecutorAvailabilityRead(
            executor="remote_gpu",
            available=True,
            reason_code=None,
            reason_message=None,
            remote_profile=self._profile.name,
            recommended=(pipeline.recommended_executor == "remote_gpu"),
        )
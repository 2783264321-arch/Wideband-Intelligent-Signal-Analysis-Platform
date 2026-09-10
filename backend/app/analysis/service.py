from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from uuid import uuid4

from app.analysis.job_manager import LocalJobManager
from app.analysis.model import AnalysisRunModel
from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.pipelines.compatibility import is_input_compatible
from app.pipelines.plugin import validate_plugin_parameters
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.executor import RemoteExecutorProbe


def mark_stale_running_runs_interrupted(session: Session) -> int:
    """Interrupt stale ``local_cpu`` running runs only.

    Remote ``remote_gpu`` runs are never blindly interrupted here; they are
    re-coordinated at startup (see ``remote_execution.recovery``).
    """
    from app.remote_execution.recovery import mark_stale_local_cpu_runs_interrupted

    return mark_stale_local_cpu_runs_interrupted(session)


class AnalysisService:
    def __init__(
        self,
        session: Session,
        registry: PipelineRegistry,
        job_manager: LocalJobManager,
        remote_executor_probe: RemoteExecutorProbe | None = None,
        *,
        remote_coordinator_launcher=None,
        identity_resolver=None,
        orchestrator_commit_resolver=None,
        model_release_store=None,
        runtime_commit_config=None,
        project_root=None,
        data_root=None,
    ):
        self.session = session
        self.registry = registry
        self.job_manager = job_manager
        self.remote_executor_probe = remote_executor_probe
        self.remote_coordinator_launcher = remote_coordinator_launcher
        self.identity_resolver = identity_resolver
        self.orchestrator_commit_resolver = orchestrator_commit_resolver
        self.model_release_store = model_release_store
        self.runtime_commit_config = runtime_commit_config
        self.project_root = project_root
        self.data_root = data_root

    def get(self, run_id: str) -> AnalysisRunModel:
        run = self.session.get(AnalysisRunModel, run_id)
        if run is None:
            raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run was not found.", 404)
        return run

    def list(self, *, recording_id: str | None = None, status: str | None = None) -> list[AnalysisRunModel]:
        statement = select(AnalysisRunModel)
        if recording_id is not None:
            statement = statement.where(AnalysisRunModel.recording_id == recording_id)
        if status is not None:
            statement = statement.where(AnalysisRunModel.status == status)
        return list(self.session.scalars(statement.order_by(AnalysisRunModel.created_at, AnalysisRunModel.id)).all())

    def executor_availability(self, recording_id: str, pipeline_id: str) -> ExecutorAvailabilityRead:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        pipeline = self.registry.get(pipeline_id)
        definition = pipeline.definition

        if "remote_gpu" not in definition.executors_supported:
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=False,
                reason_code="PIPELINE_NOT_REMOTE_CAPABLE",
                reason_message="Pipeline does not support remote GPU execution.",
                remote_profile=None,
                recommended=False,
            )

        if not is_input_compatible(definition, recording.label_space):
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=False,
                reason_code="INPUT_INCOMPATIBLE",
                reason_message="Pipeline cannot run for this recording label space.",
                remote_profile=None,
                recommended=False,
            )

        if self.remote_executor_probe is None:
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=False,
                reason_code="REMOTE_EXECUTOR_UNAVAILABLE",
                reason_message="No remote executor profile is configured.",
                remote_profile=None,
                recommended=False,
            )

        result = self.remote_executor_probe.availability(
            recording,
            definition,
            recording.source_data_sha256,
        )
        if result.available:
            return ExecutorAvailabilityRead(
                executor="remote_gpu",
                available=True,
                reason_code=None,
                reason_message=None,
                remote_profile=result.remote_profile,
                recommended=(definition.recommended_executor == "remote_gpu"),
            )
        return ExecutorAvailabilityRead(
            executor="remote_gpu",
            available=False,
            reason_code=result.reason_code or "REMOTE_EXECUTOR_UNAVAILABLE",
            reason_message=result.reason_message,
            remote_profile=result.remote_profile,
            recommended=False,
        )

    def create_run(
        self,
        *,
        recording_id: str,
        pipeline_id: str,
        executor: str,
        parameters: dict,
        model_release_id: str | None = None,
    ) -> AnalysisRunModel:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        pipeline = self.registry.get(pipeline_id)
        definition = pipeline.definition

        # The plugin's own parameter schema decides validity; no plugin-specific
        # parameter branch lives in the control plane.
        validate_plugin_parameters(definition, parameters)

        if executor == "remote_gpu":
            return self._create_remote_gpu_run(recording, definition, parameters, model_release_id)

        if executor != "local_cpu":
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Only the local_cpu executor is available in the core slice.")
        if not definition.cpu_supported:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Selected pipeline does not support local CPU execution.")
        if not is_input_compatible(definition, recording.label_space):
            raise PlatformError("INPUT_INCOMPATIBLE", "Selected pipeline cannot run for this recording label space.")

        run = AnalysisRunModel(
            id=f"run_{uuid4().hex}",
            recording_id=recording.id,
            pipeline_id=definition.id,
            pipeline_version=definition.version,
            executor=executor,
            status="pending",
            parameters_json=parameters,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)

        try:
            run.worker_pid = self.job_manager.start(run.id)
            self.session.commit()
            self.session.refresh(run)
        except Exception as exc:
            run.status = "failed"
            run.error_type = "ANALYSIS_FAILED"
            run.error_message = str(exc)[:1000]
            self.session.commit()
            raise PlatformError("ANALYSIS_FAILED", "Unable to start local analysis worker.") from exc
        return run

    def _create_remote_gpu_run(self, recording, definition, parameters, model_release_id) -> AnalysisRunModel:
        from app.remote_execution.identity import (
            resolve_local_orchestrator_commit,
            resolve_remote_recording_identity,
        )
        from app.remote_execution.request_builder import freeze_request_provenance
        from app.remote_execution.startup import build_coordinator_metadata

        if "remote_gpu" not in definition.executors_supported:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Selected pipeline does not support remote GPU execution.")
        if not is_input_compatible(definition, recording.label_space):
            raise PlatformError("INPUT_INCOMPATIBLE", "Selected pipeline cannot run for this recording label space.")

        # Consult the availability/probe service BEFORE creating/dispatching.
        availability = self.executor_availability(recording.id, definition.id)
        if not availability.available:
            raise PlatformError(
                "EXECUTOR_UNAVAILABLE",
                availability.reason_message or "Remote GPU executor is unavailable.",
            )

        run_id = f"run_{uuid4().hex}"
        if self.remote_executor_probe is None or self.identity_resolver is None:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Remote executor configuration is incomplete.")
        if self.orchestrator_commit_resolver is None or self.model_release_store is None:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Remote provenance configuration is incomplete.")
        if not self.runtime_commit_config:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Remote runtime commit is not configured.")

        # Resolve the concrete ModelRelease exactly once; explicit request overrides
        # the platform default. ``resolve`` fully verifies the referenced manifest
        # identity and self-hash (never a reverse/index lookup).
        resolved_release = self.model_release_store.resolve(
            definition.plugin_id, definition.plugin_version, model_release_id
        )
        frozen_model_release_id = resolved_release.release.model_release_id
        asset_manifest_sha256 = resolved_release.manifest.asset_manifest_sha256

        identity = self.identity_resolver(self.session, recording, self.data_root, run_id)
        orchestrator_commit = self.orchestrator_commit_resolver(self.project_root)

        frozen_metadata = freeze_request_provenance(
            local_run_id=run_id,
            recording_fingerprint=identity.recording_fingerprint,
            source_data_sha256=identity.source_data_sha256,
            dataset_name=identity.dataset_name,
            dataset_split=identity.dataset_split,
            dataset_key=identity.dataset_key,
            label_space=identity.label_space,
            pipeline_id=definition.id,
            pipeline_version=definition.version,
            required_remote_runtime_commit=self.runtime_commit_config,
            orchestrator_commit=orchestrator_commit,
            asset_manifest_sha256=asset_manifest_sha256,
            remote_profile=availability.remote_profile or "remote",
            model_release_id=frozen_model_release_id,
            parameters=parameters,
        )
        final_metadata = build_coordinator_metadata(frozen_metadata)
        coordinator_token = final_metadata["coordinator_token"]

        run = AnalysisRunModel(
            id=run_id,
            recording_id=recording.id,
            pipeline_id=definition.id,
            pipeline_version=definition.version,
            executor="remote_gpu",
            status="pending",
            parameters_json=dict(parameters),
            execution_metadata_json=final_metadata,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)

        try:
            run.worker_pid = self.remote_coordinator_launcher.launch(run.id, coordinator_token)
            self.session.commit()
            self.session.refresh(run)
        except Exception as exc:
            run.status = "failed"
            run.error_type = "ANALYSIS_FAILED"
            run.error_message = str(exc)[:1000]
            self.session.commit()
            raise PlatformError("ANALYSIS_FAILED", "Unable to launch remote coordinator.") from exc
        return run

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from uuid import uuid4

from app.analysis.job_manager import LocalJobManager
from app.analysis.model import AnalysisRunModel
from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
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
        executor_registry=None,
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
        self.executor_registry = executor_registry

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

    def executor_availability(
        self, recording_id: str, pipeline_id: str, executor: str = "remote_gpu"
    ) -> ExecutorAvailabilityRead:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        pipeline = self.registry.get(pipeline_id)
        definition = pipeline.definition
        if self.executor_registry is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE", "Executor registry is not configured."
            )
        resolved_release = self._resolve_release(definition, None)
        return self.executor_registry.availability_for(
            definition, resolved_release, recording, executor
        )

    def _resolve_release(self, definition, requested):
        """Resolve the ModelRelease for a plugin. Release-less plugins use None."""
        if not definition.model_release_required:
            if requested is not None:
                raise PlatformError(
                    "MODEL_RELEASE_MISMATCH",
                    "This plugin does not accept a model release identity.",
                )
            return None
        if self.model_release_store is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE", "Model release store is not configured."
            )
        return self.model_release_store.resolve(
            definition.plugin_id, definition.plugin_version, requested
        )

    # ---------- preparation (caller-owned transaction) ----------

    def prepare_run(
        self,
        *,
        recording_id: str,
        pipeline_id: str,
        executor: str,
        parameters: dict,
        model_release_id: str | None = None,
    ) -> AnalysisRunModel:
        """Validate/resolve/freeze and stage a pending AnalysisRun.

        Adds the Run to the CALLER'S Session. Never commits, rolls back,
        flushes, or launches: the caller owns the transaction, which is what
        lets DatasetExperiment Transaction A bind the Run, the Attempt, and the
        Item atomically (G3).
        """
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        pipeline = self.registry.get(pipeline_id)
        definition = pipeline.definition

        # The plugin's own parameter schema decides validity; no plugin-specific
        # parameter branch lives in the control plane.
        validate_plugin_parameters(definition, parameters)

        if self.executor_registry is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE", "Executor registry is not configured."
            )
        resolved_release = self._resolve_release(definition, model_release_id)

        # Exact requested-executor availability: capability + exact certificate +
        # provider probe. The platform never substitutes a different executor.
        availability = self.executor_registry.availability_for(
            definition, resolved_release, recording, executor
        )
        if not availability.available:
            raise PlatformError(
                availability.reason_code or "EXECUTOR_UNAVAILABLE",
                availability.reason_message or "The requested executor is unavailable.",
            )
        provider = self.executor_registry.provider(executor)

        if executor == "remote_gpu":
            return self._prepare_remote_run(
                recording, definition, parameters, resolved_release, provider, availability
            )
        return self._prepare_local_run(
            recording, definition, parameters, resolved_release, provider
        )

    def _prepare_local_run(self, recording, definition, parameters, resolved_release, provider) -> AnalysisRunModel:
        run_id = f"run_{uuid4().hex}"
        metadata = {"runtime_descriptor": provider.runtime_descriptor().to_metadata()}
        if resolved_release is not None:
            metadata["model_release_id"] = resolved_release.release.model_release_id
            metadata["asset_manifest_sha256"] = resolved_release.manifest.asset_manifest_sha256

        run = AnalysisRunModel(
            id=run_id,
            recording_id=recording.id,
            pipeline_id=definition.id,
            pipeline_version=definition.version,
            executor=provider.name,
            status="pending",
            parameters_json=dict(parameters),
            execution_metadata_json=metadata,
        )
        self.session.add(run)
        return run

    def _prepare_remote_run(self, recording, definition, parameters, resolved_release, provider, availability) -> AnalysisRunModel:
        run_id = f"run_{uuid4().hex}"
        final_metadata = self._freeze_remote_provenance(
            run_id=run_id,
            recording=recording,
            definition=definition,
            resolved_release=resolved_release,
            provider=provider,
            availability=availability,
            parameters=parameters,
        )
        run = AnalysisRunModel(
            id=run_id,
            recording_id=recording.id,
            pipeline_id=definition.id,
            pipeline_version=definition.version,
            executor=provider.name,
            status="pending",
            parameters_json=dict(parameters),
            execution_metadata_json=final_metadata,
        )
        self.session.add(run)
        return run

    def _freeze_remote_provenance(self, *, run_id, recording, definition, resolved_release, provider, availability, parameters) -> dict:
        from app.remote_execution.request_builder import freeze_request_provenance
        from app.remote_execution.startup import build_coordinator_metadata

        if resolved_release is None:
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH", "Remote execution requires a ModelRelease."
            )
        if self.identity_resolver is None or self.orchestrator_commit_resolver is None:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Remote provenance configuration is incomplete.")
        if not self.runtime_commit_config:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Remote runtime commit is not configured.")

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
        # runtime_descriptor is internal execution metadata OUTSIDE the canonical
        # request payload; request_sha256 and recovery reconstruction are unchanged.
        frozen_metadata["runtime_descriptor"] = provider.runtime_descriptor().to_metadata()
        return build_coordinator_metadata(frozen_metadata)

    # ---------- physical launch of an already-persisted prepared run ----------

    def launch_prepared_run(self, run_id: str) -> AnalysisRunModel:
        """Physically launch an already-persisted prepared AnalysisRun.

        Durable commit ordering is a CALLER precondition:
            G3-A Transaction A COMMIT
            G3-B Transaction B COMMIT
            launch_prepared_run(...)

        This method does not prove transaction durability from ORM object state.
        It only defensively rejects a Run that is still staged/unflushed in this
        Session (checked BEFORE any query, so no autoflush can occur), then loads
        the persisted Run and launches it. It never constructs a new Run, never
        rebuilds remote provenance, and never regenerates the coordinator token.
        """
        # Defensive pre-query guard: a Run still staged in this Session has no
        # durable row and must not be launched. Durable commit ordering itself is
        # guaranteed by the caller contract, not by Session.new.
        for pending in self.session.new:
            if isinstance(pending, AnalysisRunModel) and pending.id == run_id:
                raise PlatformError(
                    "ANALYSIS_RUN_NOT_LAUNCHABLE",
                    "Prepared analysis run is still staged in this session; the caller must commit before launch.",
                    409,
                )

        run = self.get(run_id)  # raises ANALYSIS_RUN_NOT_FOUND (404)
        if run.worker_pid is not None or run.status != "pending":
            raise PlatformError(
                "ANALYSIS_RUN_NOT_LAUNCHABLE",
                "Only a pending, not-yet-launched prepared analysis run can be launched.",
                409,
            )
        if self.executor_registry is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE", "Executor registry is not configured."
            )
        provider = self.executor_registry.provider(run.executor)

        coordinator_token = None
        if run.executor == "remote_gpu":
            coordinator_token = (run.execution_metadata_json or {}).get("coordinator_token")
            if not coordinator_token:
                raise PlatformError(
                    "ANALYSIS_RUN_NOT_LAUNCHABLE",
                    "Prepared remote run is missing its coordinator token.",
                    409,
                )
        try:
            run.worker_pid = provider.launch(run.id, coordinator_token=coordinator_token)
            self.session.commit()
            self.session.refresh(run)
        except Exception as exc:
            run.status = "failed"
            run.error_type = "ANALYSIS_FAILED"
            run.error_message = str(exc)[:1000]
            self.session.commit()
            if run.executor == "remote_gpu":
                raise PlatformError(
                    "ANALYSIS_FAILED", "Unable to launch remote coordinator."
                ) from exc
            raise PlatformError(
                "ANALYSIS_FAILED", "Unable to launch local inference worker."
            ) from exc
        return run

    def create_run(
        self,
        *,
        recording_id: str,
        pipeline_id: str,
        executor: str,
        parameters: dict,
        model_release_id: str | None = None,
    ) -> AnalysisRunModel:
        """Legacy Single-Recording path.

        Composes the caller-owned prepare seam with a service-owned preparation
        commit and the physical launch primitive, preserving current public
        behavior exactly.
        """
        run = self.prepare_run(
            recording_id=recording_id,
            pipeline_id=pipeline_id,
            executor=executor,
            parameters=parameters,
            model_release_id=model_release_id,
        )
        self.session.commit()
        self.session.refresh(run)
        return self.launch_prepared_run(run.id)


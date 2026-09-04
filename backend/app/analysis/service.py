from __future__ import annotations

from sqlalchemy.orm import Session
from uuid import uuid4

from app.analysis.job_manager import LocalJobManager
from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel


class AnalysisService:
    def __init__(self, session: Session, registry: PipelineRegistry, job_manager: LocalJobManager):
        self.session = session
        self.registry = registry
        self.job_manager = job_manager

    def get(self, run_id: str) -> AnalysisRunModel:
        run = self.session.get(AnalysisRunModel, run_id)
        if run is None:
            raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run was not found.", 404)
        return run

    def create_run(
        self,
        *,
        recording_id: str,
        pipeline_id: str,
        executor: str,
        parameters: dict,
    ) -> AnalysisRunModel:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        pipeline = self.registry.get(pipeline_id)
        definition = pipeline.definition

        if executor != "local_cpu":
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Only the local_cpu executor is available in the core slice.")
        if not definition.cpu_supported:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Selected pipeline does not support local CPU execution.")
        if recording.label_space != definition.label_space:
            raise PlatformError("PIPELINE_INCOMPATIBLE", "Selected pipeline cannot run for this recording label space.")

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

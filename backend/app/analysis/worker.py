from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import sys
import traceback

from app.analysis.model import AnalysisRunModel
from app.core.config import Settings
from app.core.errors import PlatformError
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.labels.service import LabelSpaceService
from app.pipelines.base import RecordingInput
from app.pipelines.registry import create_pipeline_registry
from app.recordings.model import RecordingModel
from app.remote_execution.validation import AnalysisResultWriter
from app.storage.service import StorageService

logger = logging.getLogger(__name__)


def _recording_input(recording: RecordingModel, data_root: Path) -> RecordingInput:
    if recording.external_path:
        data_path = Path(recording.external_path).resolve()
    else:
        data_path = (data_root / recording.data_path).resolve()
    return RecordingInput(
        id=recording.id,
        data_path=data_path,
        data_format=recording.data_format,
        sample_rate_hz=recording.sample_rate_hz,
        center_frequency_hz=recording.center_frequency_hz,
        frequency_low_hz=recording.frequency_low_hz,
        frequency_high_hz=recording.frequency_high_hz,
        duration_s=recording.duration_s,
        label_space=recording.label_space,
    )


def execute_run(run_id: str, settings: Settings | None = None) -> None:
    settings = settings or Settings()
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    registry = create_pipeline_registry()
    label_service = LabelSpaceService(settings.label_space_root)
    storage = StorageService(settings.data_root)

    try:
        with database.session_factory() as session:
            run = session.get(AnalysisRunModel, run_id)
            if run is None:
                raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run was not found.", 404)
            recording = session.get(RecordingModel, run.recording_id)
            if recording is None:
                raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)

            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            run.error_type = None
            run.error_message = None
            session.commit()

            pipeline = registry.get(run.pipeline_id)
            workspace = storage.artifact_dir(run.id)
            output = pipeline.run(_recording_input(recording, settings.data_root), dict(run.parameters_json), workspace)

            writer = AnalysisResultWriter(
                session=session,
                label_service=label_service,
                pipeline_definition=pipeline.definition,
                workspace=workspace,
            )
            writer.persist(
                run=run,
                recording=recording,
                output=output,
            )
            session.commit()
    except Exception as exc:
        logger.error("Analysis worker failed for %s\n%s", run_id, traceback.format_exc())
        with database.session_factory() as recovery_session:
            run = recovery_session.get(AnalysisRunModel, run_id)
            if run is not None:
                run.status = "failed"
                if isinstance(exc, PlatformError):
                    run.error_type = exc.code
                    run.error_message = exc.message[:1000]
                else:
                    run.error_type = type(exc).__name__
                    run.error_message = str(exc)[:1000]
                run.finished_at = datetime.now(timezone.utc)
                recovery_session.commit()
        raise


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("usage: python -m app.analysis.worker <run_id>", file=sys.stderr)
        return 2
    execute_run(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

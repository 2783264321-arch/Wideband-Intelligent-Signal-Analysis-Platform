from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.core.signal_validation import validate_label, validate_physical_box
from app.detections.model import DetectionResultModel
from app.imported_runs.archive import extract_package, invalid, read_json, safe_path
from app.imported_runs.schema import Manifest, PackageDetection
from app.labels.service import LabelSpaceService
from app.recordings.model import RecordingModel
from app.storage.service import StorageService


class PackageImportService:
    def __init__(self, session: Session, storage: StorageService, labels: LabelSpaceService):
        self.session = session
        self.storage = storage
        self.labels = labels

    def import_run(self, source: BinaryIO, recording_id: str) -> AnalysisRunModel:
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Choose an existing local Recording.", 404)
        imports = self.storage.data_root / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="staging-", dir=imports) as temporary:
            root = extract_package(source, Path(temporary))
            try:
                manifest = Manifest.model_validate(read_json(root / "manifest.json"))
                self._match_recording(manifest, recording)
                if Path(manifest.results.detections).name != "detections.json":
                    raise invalid("The detections result must point to detections.json.")
                detections_doc = read_json(safe_path(root, manifest.results.detections))
                if not isinstance(detections_doc, dict) or not isinstance(detections_doc.get("detections"), list):
                    raise invalid("detections.json must contain a 'detections' array.")
                detections = TypeAdapter(list[PackageDetection]).validate_python(
                    detections_doc["detections"])
                if len(detections) > 100000:
                    raise invalid("A package may contain at most 100000 detections.")
                source_ids = [item.id for item in detections if item.id is not None]
                if len(source_ids) != len(set(source_ids)):
                    raise invalid("Detection source ids must be unique within the package.")
                for item in detections:
                    validate_physical_box(recording, **item.model_dump(include={
                        "t_start_s", "t_end_s", "f_low_hz", "f_high_hz"}),
                        error_code="INVALID_IMPORT_PACKAGE")
                    validate_label(self.labels, label_space_id=manifest.label_space,
                                   class_id=item.class_id, class_name=item.class_name,
                                   error_code="INVALID_IMPORT_PACKAGE")
            except ValidationError as exc:
                raise invalid("Package schema or execution metadata is invalid.") from exc

            run_id = f"run_{uuid4().hex}"
            models = [DetectionResultModel(
                id=f"det_{uuid4().hex}", run_id=run_id,
                **item.model_dump(exclude={"id", "scores"}), scores_json=item.scores,
            ) for item in detections]
            id_map = {item.id: model.id for item, model in zip(detections, models) if item.id is not None}

            # All validation precedes the first ORM write. Files are staged on the
            # same filesystem and promoted only after every check has passed.
            destination = imports / run_id
            run = AnalysisRunModel(
                id=run_id, recording_id=recording.id, pipeline_id=manifest.pipeline.id,
                pipeline_version=manifest.pipeline.version, executor="imported", status="completed",
                parameters_json={
                    "package": {
                        "pipeline_id": manifest.pipeline.id,
                        "pipeline_name": manifest.pipeline.name,
                        "pipeline_version": manifest.pipeline.version,
                        "recording_name": manifest.recording.name,
                        "dataset": manifest.recording.dataset,
                    },
                    "source_detection_ids": id_map,
                    "detection_count": len(detections),
                },
                hardware_info_json={
                    "executor": manifest.execution.executor,
                    "device": manifest.execution.device,
                    "environment": manifest.execution.environment,
                },
                created_at=datetime.now(timezone.utc),
            )
            try:
                root.rename(destination)
                self.session.add_all([run, *models])
                self.session.commit()
            except Exception:
                self.session.rollback()
                if destination.is_dir():
                    import shutil
                    shutil.rmtree(destination)
                raise
            self.session.refresh(run)
            return run

    def _match_recording(self, manifest: Manifest, recording: RecordingModel) -> None:
        if manifest.label_space != recording.label_space:
            raise invalid("Package label_space does not match the selected Recording.")
        self.labels.get(manifest.label_space)

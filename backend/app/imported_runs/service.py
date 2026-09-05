from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.imported_runs.archive import extract_package
from app.imported_runs.factory import build_imported_run_models
from app.imported_runs.validation import validate_extracted_package
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
            validated = validate_extracted_package(root, recording, self.labels)

            run_id = f"run_{uuid4().hex}"
            detection_ids = [f"det_{uuid4().hex}" for _ in validated.detections]
            built = build_imported_run_models(
                recording, validated, run_id=run_id, detection_ids=detection_ids)

            # All validation precedes the first ORM write. Files are staged on the
            # same filesystem and promoted only after every check has passed.
            destination = imports / run_id
            try:
                root.rename(destination)
                self.session.add_all([built.run, *built.detections])
                self.session.commit()
            except Exception:
                self.session.rollback()
                if destination.is_dir():
                    import shutil
                    shutil.rmtree(destination)
                raise
            self.session.refresh(built.run)
            return built.run
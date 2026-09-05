from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app.core.signal_validation import validate_label, validate_physical_box
from app.imported_runs.archive import invalid, read_json, safe_path
from app.imported_runs.schema import Manifest, PackageDetection
from app.labels.service import LabelSpaceService
from app.recordings.model import RecordingModel

MAX_CHILD_DETECTIONS = 100000


@dataclass(frozen=True)
class ValidatedAnalysisPackage:
    manifest: Manifest
    detections: tuple[PackageDetection, ...]


def validate_extracted_package(
    root: Path,
    recording: RecordingModel,
    labels: LabelSpaceService,
) -> ValidatedAnalysisPackage:
    try:
        manifest = Manifest.model_validate(read_json(root / "manifest.json"))
        if manifest.label_space != recording.label_space:
            raise invalid("Package label_space does not match the selected Recording.")
        labels.get(manifest.label_space)
        if Path(manifest.results.detections).name != "detections.json":
            raise invalid("The detections result must point to detections.json.")
        detections_doc = read_json(safe_path(root, manifest.results.detections))
        if not isinstance(detections_doc, dict) or not isinstance(detections_doc.get("detections"), list):
            raise invalid("detections.json must contain a 'detections' array.")
        detections = TypeAdapter(list[PackageDetection]).validate_python(detections_doc["detections"])
        if len(detections) > MAX_CHILD_DETECTIONS:
            raise invalid(f"A package may contain at most {MAX_CHILD_DETECTIONS} detections.")
        source_ids = [item.id for item in detections if item.id is not None]
        if len(source_ids) != len(set(source_ids)):
            raise invalid("Detection source ids must be unique within the package.")
        for item in detections:
            validate_physical_box(recording, **item.model_dump(include={
                "t_start_s", "t_end_s", "f_low_hz", "f_high_hz"
            }), error_code="INVALID_IMPORT_PACKAGE")
            validate_label(
                labels,
                label_space_id=manifest.label_space,
                class_id=item.class_id,
                class_name=item.class_name,
                error_code="INVALID_IMPORT_PACKAGE",
            )
    except ValidationError as exc:
        raise invalid("Package schema or execution metadata is invalid.") from exc
    return ValidatedAnalysisPackage(manifest=manifest, detections=tuple(detections))
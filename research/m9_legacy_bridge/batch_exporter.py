"""Generic deterministic Batch Analysis Package v1 writer.

The writer knows only the batch transport format; it must not know YOLO, AHLP,
FRN, or RT-DETR internals.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import zipfile

from app.imported_runs.batch_schema import BatchItem, BatchManifest
from app.imported_runs.schema import Manifest, PackageDetection

FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class BatchExportItem:
    item: BatchItem
    child_manifest: Manifest
    detections: tuple[PackageDetection, ...]


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def _write_json_member(archive: zipfile.ZipFile, name: str, payload: object) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, _json_bytes(payload))


def export_batch_package(
    output_path: Path,
    manifest: BatchManifest,
    items: tuple[BatchExportItem, ...],
) -> Path:
    outer_keys = {item.key for item in manifest.items}
    item_keys = {export.item.key for export in items}
    if outer_keys != item_keys:
        raise ValueError("BatchExportItem key set must equal the outer BatchManifest item key set.")
    by_key = {export.item.key: export for export in items}
    manifest_by_key = {item.key: item for item in manifest.items}
    for key in manifest_by_key:
        export = by_key[key]
        if export.item.package_path != manifest_by_key[key].package_path:
            raise ValueError("BatchExportItem package_path must match the outer manifest.")
        if export.item.recording.name != manifest_by_key[key].recording.name:
            raise ValueError("BatchExportItem Recording name must match the outer manifest.")

    output_path = Path(output_path)
    with zipfile.ZipFile(output_path, "w") as archive:
        _write_json_member(archive, "batch_manifest.json", manifest.model_dump())
        for key in sorted(manifest_by_key):
            export = by_key[key]
            child_dir = manifest_by_key[key].package_path
            _write_json_member(archive, f"{child_dir}/manifest.json", export.child_manifest.model_dump())
            _write_json_member(archive, f"{child_dir}/detections.json", {
                "detections": [detection.model_dump() for detection in export.detections],
            })
    return output_path
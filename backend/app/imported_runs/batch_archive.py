"""Batch-scale bounded safe extraction; keeps M6 single-package limits unchanged."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import stat
from typing import BinaryIO
import zipfile

from app.core.errors import PlatformError
from app.imported_runs.archive import safe_path

MAX_BATCH_ITEMS = 10_000
MAX_TOTAL_DETECTIONS = 1_000_000
MAX_BATCH_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_BATCH_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_BATCH_MEMBERS = 25_000
MAX_JSON_BYTES = 32 * 1024 * 1024


def invalid_batch(message: str, *, details: dict[str, object] | None = None) -> PlatformError:
    return PlatformError(
        "INVALID_BATCH_IMPORT_PACKAGE",
        message,
        400,
        details={} if details is None else details,
    )


def _safe_path(root: Path, name: str) -> Path:
    try:
        return safe_path(root, name)
    except PlatformError as exc:
        raise invalid_batch(exc.message) from exc


def extract_batch_package(source: BinaryIO, destination: Path) -> Path:
    source.seek(0, 2)
    if source.tell() > MAX_BATCH_UPLOAD_BYTES:
        raise invalid_batch("ZIP exceeds the 256 MiB batch upload limit.")
    source.seek(0)
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > MAX_BATCH_MEMBERS or sum(m.file_size for m in members) > MAX_BATCH_EXPANDED_BYTES:
                raise invalid_batch("ZIP exceeds the batch extraction size or file-count limit.")
            paths = set()
            planned = []
            for member in members:
                name = member.filename.rstrip("/") if member.is_dir() else member.filename
                target = _safe_path(destination, name)
                kind = stat.S_IFMT(member.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or member.flag_bits & 1:
                    raise invalid_batch("Links, special files, and encrypted ZIP members are not supported.")
                if name.casefold() in paths:
                    raise invalid_batch("ZIP contains duplicate or case-colliding paths.")
                paths.add(name.casefold())
                planned.append((member, target))
            for member, target in planned:
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source_file, target.open("xb") as output:
                        shutil.copyfileobj(source_file, output, length=1024 * 1024)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError) as exc:
        raise invalid_batch("ZIP is invalid, unreadable, or contains conflicting paths.") from exc
    if (destination / "batch_manifest.json").is_file():
        return destination
    raise invalid_batch("Package must contain batch_manifest.json at its root.")


def read_batch_json(path: Path):
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise invalid_batch("JSON exceeds the 32 MiB limit.")
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                          object_pairs_hook=unique_object)
    except (OSError, ValueError, RecursionError) as exc:
        raise invalid_batch(f"Required JSON is missing or invalid: {path.name}") from exc
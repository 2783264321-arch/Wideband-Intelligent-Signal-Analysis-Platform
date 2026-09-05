"""Bounded ZIP extraction into a caller-owned temporary directory."""
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import BinaryIO
import zipfile

from app.core.errors import PlatformError

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 2048
MAX_JSON_BYTES = 32 * 1024 * 1024


def invalid(message: str) -> PlatformError:
    return PlatformError("INVALID_IMPORT_PACKAGE", message)


def safe_path(root: Path, name: str) -> Path:
    # Reject Windows aliases/ADS as well as POSIX traversal, on every OS.
    parts = name.split("/")
    if (not name or "\\" in name or any(
        not part or part in {".", ".."} or part.endswith((".", " "))
        or re.search(r'[<>:"|?*\x00-\x1f]', part)
        or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)
        for part in parts
    )):
        raise invalid("Package contains an unsafe relative path.")
    target = root.joinpath(*PurePosixPath(name).parts).resolve()
    if root.resolve() not in target.parents:
        raise invalid("Package path escapes its directory.")
    return target


def extract_package(source: BinaryIO, destination: Path) -> Path:
    source.seek(0, 2)
    if source.tell() > MAX_UPLOAD_BYTES:
        raise invalid("ZIP exceeds the 256 MiB upload limit.")
    source.seek(0)
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS or sum(m.file_size for m in members) > MAX_EXPANDED_BYTES:
                raise invalid("ZIP exceeds the extraction size or file-count limit.")
            paths = set()
            planned = []
            for member in members:
                name = member.filename.rstrip("/") if member.is_dir() else member.filename
                target = safe_path(destination, name)
                kind = stat.S_IFMT(member.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or member.flag_bits & 1:
                    raise invalid("Links, special files, and encrypted ZIP members are not supported.")
                if name.casefold() in paths:
                    raise invalid("ZIP contains duplicate or case-colliding paths.")
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
        raise invalid("ZIP is invalid, unreadable, or contains conflicting paths.") from exc
    if (destination / "manifest.json").is_file():
        return destination
    children = list(destination.iterdir())
    if len(children) == 1 and children[0].is_dir() and (children[0] / "manifest.json").is_file():
        return children[0]
    raise invalid("Package must contain manifest.json at its root (or inside one wrapper directory).")


def read_json(path: Path):
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
            raise invalid("JSON exceeds the 32 MiB limit.")
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                          object_pairs_hook=unique_object)
    except (OSError, ValueError, RecursionError) as exc:
        raise invalid(f"Required JSON is missing or invalid: {path.name}") from exc

from io import BytesIO
import zipfile

import pytest

from app.core.errors import PlatformError
from app.imported_runs.batch_archive import (
    MAX_BATCH_EXPANDED_BYTES,
    MAX_BATCH_ITEMS,
    MAX_BATCH_MEMBERS,
    MAX_BATCH_UPLOAD_BYTES,
    MAX_JSON_BYTES,
    MAX_TOTAL_DETECTIONS,
    extract_batch_package,
)


def _zip(members: dict[str, bytes], *, compress=zipfile.ZIP_STORED) -> BytesIO:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(zipfile.ZipInfo(name), data, compress_type=compress)
    buffer.seek(0)
    return buffer


def _encrypted_zip() -> BytesIO:
    """Build a ZIP whose first central-directory entry has the encrypted flag set."""
    raw = _zip({"batch_manifest.json": b"{}"}).getvalue()
    data = bytearray(raw)
    # End of central directory record: PK\x05\x06 at the tail; central dir offset
    # is a 4-byte little-endian field 16 bytes into the EOCD.
    eocd = data.rfind(b"PK\x05\x06")
    central_offset = int.from_bytes(data[eocd + 16:eocd + 20], "little")
    # First central directory file header: PK\x01\x02, flags at offset +8.
    if data[central_offset:central_offset + 4] != b"PK\x01\x02":
        raise AssertionError("expected central directory file header")
    data[central_offset + 8] |= 0x01
    return BytesIO(bytes(data))


def test_batch_constants_match_approved_limits():
    assert MAX_BATCH_ITEMS == 10_000
    assert MAX_TOTAL_DETECTIONS == 1_000_000
    assert MAX_BATCH_UPLOAD_BYTES == 256 * 1024 * 1024
    assert MAX_BATCH_EXPANDED_BYTES == 1024 * 1024 * 1024
    assert MAX_BATCH_MEMBERS == 25_000
    assert MAX_JSON_BYTES == 32 * 1024 * 1024


def test_root_batch_manifest_accepted(tmp_path):
    members = {"batch_manifest.json": b'{"schema_version": 1}', "items/000000/manifest.json": b'{"a":1}'}
    root = extract_batch_package(_zip(members), tmp_path / "out")
    assert (root / "batch_manifest.json").is_file()


def test_traversal_rejected(tmp_path):
    members = {"batch_manifest.json": b"{}", "../escape": b"x"}
    with pytest.raises(PlatformError) as exc:
        extract_batch_package(_zip(members), tmp_path / "out")
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_windows_reserved_and_ads_names_rejected(tmp_path):
    for unsafe in ("CON", "file:stream", "aux.txt"):
        members = {"batch_manifest.json": b"{}", unsafe: b"x"}
        with pytest.raises(PlatformError) as exc:
            extract_batch_package(_zip(members), tmp_path / "out")
        assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_safe_path_rejects_backslash_names(tmp_path):
    from app.imported_runs.batch_archive import _safe_path
    with pytest.raises(PlatformError) as exc:
        _safe_path(tmp_path / "out", "items\\escape")
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_case_collision_rejected(tmp_path):
    members = {"batch_manifest.json": b"{}", "items/A": b"1", "items/a": b"2"}
    with pytest.raises(PlatformError):
        extract_batch_package(_zip(members), tmp_path / "out")


def test_symlink_member_rejected(tmp_path):
    info = zipfile.ZipInfo("batch_manifest.json")
    info.create_system = 3
    info.external_attr = (0o120777 << 16)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, b"{}")
        archive.writestr("items/x", b"1")
    buffer.seek(0)
    with pytest.raises(PlatformError):
        extract_batch_package(buffer, tmp_path / "out")


def test_encrypted_member_rejected(tmp_path):
    with pytest.raises(PlatformError):
        extract_batch_package(_encrypted_zip(), tmp_path / "out")


def test_corrupt_zip_rejected(tmp_path):
    bad = BytesIO(b"this is not a zip file at all")
    with pytest.raises(PlatformError):
        extract_batch_package(bad, tmp_path / "out")


def test_too_many_members_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.imported_runs.batch_archive.MAX_BATCH_MEMBERS", 2)
    members = {"batch_manifest.json": b"{}", "a": b"1", "b": b"2"}
    with pytest.raises(PlatformError):
        extract_batch_package(_zip(members), tmp_path / "out")


def test_expanded_size_bound_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.imported_runs.batch_archive.MAX_BATCH_EXPANDED_BYTES", 4)
    members = {"batch_manifest.json": b"0123456789"}
    with pytest.raises(PlatformError):
        extract_batch_package(_zip(members), tmp_path / "out")


def test_upload_size_bound_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("app.imported_runs.batch_archive.MAX_BATCH_UPLOAD_BYTES", 4)
    members = {"batch_manifest.json": b"0123456789"}
    with pytest.raises(PlatformError):
        extract_batch_package(_zip(members), tmp_path / "out")


def test_missing_root_manifest_rejected(tmp_path):
    members = {"items/000000/manifest.json": b"{}"}
    with pytest.raises(PlatformError):
        extract_batch_package(_zip(members), tmp_path / "out")
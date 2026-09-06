import hashlib

import pytest

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel
from app.remote_execution.source_hash import compute_file_sha256, resolve_source_data_sha256


def _add_recording(session, *, recording_id="rec_x", data_path="raw.iq", external_path=None,
                   source_data_sha256=None):
    recording = RecordingModel(
        id=recording_id,
        name="x",
        data_path=data_path,
        data_format="complex64_le",
        source="custom",
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2_441_000_000.0,
        frequency_low_hz=2_440_500_000.0,
        frequency_high_hz=2_441_500_000.0,
        num_samples=100000,
        duration_s=0.1,
        dataset_name=None,
        dataset_split=None,
        label_space=None,
        has_ground_truth=False,
    )
    if external_path is not None:
        recording.external_path = external_path
    if source_data_sha256 is not None:
        recording.source_data_sha256 = source_data_sha256
    session.add(recording)
    session.commit()
    return recording


def test_compute_file_sha256_matches_manual_hash(tmp_path):
    blob = b"0123456789abcdef" * 100000
    path = tmp_path / "raw.iq"
    path.write_bytes(blob)
    assert compute_file_sha256(path) == hashlib.sha256(blob).hexdigest()


def test_compute_file_sha256_matches_manual_hash_with_small_chunk(tmp_path):
    blob = b"0123456789abcdef" * 1000
    path = tmp_path / "raw.iq"
    path.write_bytes(blob)
    assert compute_file_sha256(path, chunk_size=13) == hashlib.sha256(blob).hexdigest()


def test_relative_data_path_resolution_and_persistence(session, tmp_path):
    blob = b"\x00\x01\x02\x03" * 10000
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    raw_path = data_root / "raw.iq"
    raw_path.write_bytes(blob)
    recording = _add_recording(session, data_path="raw.iq", external_path=None)

    result = resolve_source_data_sha256(session, recording, data_root)
    expected = hashlib.sha256(blob).hexdigest()
    assert result == expected
    assert recording.source_data_sha256 == expected

    session.expire(recording)
    reloaded = session.get(RecordingModel, recording.id)
    assert reloaded.source_data_sha256 == expected


def test_cached_value_means_no_second_file_read(session, tmp_path, monkeypatch):
    blob = b"\x00\x01" * 5000
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    raw_path = data_root / "raw.iq"
    raw_path.write_bytes(blob)
    recording = _add_recording(session, data_path="raw.iq", external_path=None)

    import app.remote_execution.source_hash as source_hash

    calls = []
    original = source_hash.compute_file_sha256
    monkeypatch.setattr(
        source_hash, "compute_file_sha256",
        lambda path: calls.append(str(path)) or original(path),
    )

    first = resolve_source_data_sha256(session, recording, data_root)
    assert first == hashlib.sha256(blob).hexdigest()
    assert len(calls) == 1

    session.expire(recording)
    recording = session.get(RecordingModel, recording.id)
    second = resolve_source_data_sha256(session, recording, data_root)
    assert second == first
    assert len(calls) == 1  # cache hit must not re-read the file


def test_external_path_takes_priority(session, tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    relative_blob = b"relative-data" * 1000
    external_blob = b"external-data" * 1000
    (data_root / "relative.iq").write_bytes(relative_blob)
    external_file = tmp_path / "external.iq"
    external_file.write_bytes(external_blob)

    recording = _add_recording(session, data_path="relative.iq", external_path=str(external_file))
    result = resolve_source_data_sha256(session, recording, data_root)
    assert result == hashlib.sha256(external_blob).hexdigest()


def test_missing_source_file_fails_before_cache_mutation(session, tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    recording = _add_recording(session, data_path="missing.iq", external_path=None)

    with pytest.raises(PlatformError) as exc:
        resolve_source_data_sha256(session, recording, data_root)
    assert exc.value.code == "SOURCE_DATA_NOT_FOUND"

    session.expire(recording)
    recording = session.get(RecordingModel, recording.id)
    assert recording.source_data_sha256 is None


def test_directory_not_accepted_as_source_data(session, tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    dir_path = data_root / "a_directory"
    dir_path.mkdir()
    recording = _add_recording(session, data_path="a_directory", external_path=None)

    with pytest.raises(PlatformError) as exc:
        resolve_source_data_sha256(session, recording, data_root)
    assert exc.value.code == "SOURCE_DATA_NOT_FILE"

    session.expire(recording)
    recording = session.get(RecordingModel, recording.id)
    assert recording.source_data_sha256 is None


@pytest.mark.parametrize("bad", [0, -1])
def test_chunk_size_guard(tmp_path, bad):
    path = tmp_path / "raw.iq"
    path.write_bytes(b"x")
    with pytest.raises(ValueError):
        compute_file_sha256(path, chunk_size=bad)
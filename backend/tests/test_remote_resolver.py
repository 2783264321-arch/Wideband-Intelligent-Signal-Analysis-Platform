"""M9.1-B Task 9 SpaceNet remote resolver tests.

Uses a synthetic SpaceNet dataset under ``tmp_path`` and exercises the
double-identity (semantic recording fingerprint + exact raw-IQ byte hash)
contract plus the no-GroundTruth-leakage invariant. The resolver must be
SpaceNet-specific and never accept an arbitrary client data path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.core.errors import PlatformError
from app.imported_runs.fingerprint import build_recording_fingerprint
from app.pipelines.base import RecordingInput
from app.remote_execution.resolver import ResolvedSpaceNetInput, resolve_space_net

LABEL_ROOT = Path(__file__).resolve().parents[2] / "label_spaces"
OBS_LOW_MHZ = 2401.0
OBS_HIGH_MHZ = 2431.0
SAMPLE_RATE_HZ = (OBS_HIGH_MHZ - OBS_LOW_MHZ) * 1e6
NUM_SAMPLES = 3000
DURATION_S = NUM_SAMPLES / SAMPLE_RATE_HZ


def _bin_bytes() -> bytes:
    # float16 interleaved I/Q pairs (4 bytes per complex sample).
    values = np.arange(NUM_SAMPLES * 2, dtype=np.float32) * 0.001
    return values.astype("<f2").tobytes()


def _bin_bytes_variant() -> bytes:
    # Same byte length as _bin_bytes(), but different byte content.
    values = np.arange(NUM_SAMPLES * 2, dtype=np.float32) * 0.002 + 0.5
    return values.astype("<f2").tobytes()


def _metadata(class_id: int = 9) -> dict:
    return {
        "observation_range": [OBS_LOW_MHZ, OBS_HIGH_MHZ],
        "signals": [
            {
                "signal_id": 0,
                "start_frequency": 2417.97385,
                "end_frequency": 2418.02615,
                "start_time": 0.0,
                "end_time": 0.01,
                "class": class_id,
            }
        ],
    }


def _write_sample(
    root: Path,
    *,
    split: str = "test",
    stem: str = "a",
    bin_bytes: bytes | None = None,
    metadata: dict | None = None,
) -> tuple[Path, Path]:
    split_root = root / split
    split_root.mkdir(parents=True, exist_ok=True)
    bin_path = split_root / f"{stem}.bin"
    json_path = split_root / f"{stem}.json"
    bin_path.write_bytes(bin_bytes if bin_bytes is not None else _bin_bytes())
    json_path.write_text(json.dumps(metadata if metadata is not None else _metadata()), encoding="utf-8")
    return bin_path, json_path


def _expected_fingerprint(bin_path: Path, *, class_id: int = 9) -> str:
    adapter_sample = _load_sample(bin_path)
    ground_truth = tuple(
        ManifestGroundTruth(
            t_start_s=signal.t_start_s,
            t_end_s=signal.t_end_s,
            f_low_hz=signal.f_low_hz,
            f_high_hz=signal.f_high_hz,
            class_id=signal.class_id,
            class_name=signal.class_name,
        )
        for signal in adapter_sample.signals
    )
    recording = ManifestRecording(
        recording_id="local-dummy",
        name=adapter_sample.id,
        data_format=adapter_sample.data_format,
        sample_rate_hz=adapter_sample.sample_rate_hz,
        center_frequency_hz=adapter_sample.center_frequency_hz,
        frequency_low_hz=adapter_sample.frequency_low_hz,
        frequency_high_hz=adapter_sample.frequency_high_hz,
        num_samples=adapter_sample.num_samples,
        duration_s=adapter_sample.duration_s,
        ground_truth=ground_truth,
    )
    return build_recording_fingerprint("SpaceNet", "test", "spacenet_14", recording).sha256


def _load_sample(bin_path: Path):
    from app.datasets.spacenet import SpaceNetAdapter

    root = bin_path.parent.parent
    return SpaceNetAdapter(root, LABEL_ROOT, label_space_id="spacenet_14").load("test", bin_path.stem)


def _resolve(tmp_path: Path, *, key="a", expected_source_hash=None, expected_fingerprint=None):
    return resolve_space_net(
        dataset_root=tmp_path,
        split="test",
        key=key,
        label_space="spacenet_14",
        expected_fingerprint=expected_fingerprint or _expected_fingerprint(tmp_path / "test" / "a.bin"),
        expected_source_hash=expected_source_hash or hashlib.sha256(_bin_bytes()).hexdigest(),
        label_space_root=LABEL_ROOT,
    )


# ---------------------------------------------------------------------------
# A. both identities verify
# ---------------------------------------------------------------------------


def test_both_identities_verify(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    expected_source = hashlib.sha256(bin_path.read_bytes()).hexdigest()
    expected_fingerprint = _expected_fingerprint(bin_path)

    resolved = _resolve(
        tmp_path,
        expected_source_hash=expected_source,
        expected_fingerprint=expected_fingerprint,
    )

    assert isinstance(resolved, ResolvedSpaceNetInput)
    assert resolved.source_data_sha256 == expected_source
    assert resolved.recording_fingerprint == expected_fingerprint
    assert isinstance(resolved.recording_input, RecordingInput)
    assert resolved.recording_input.id == "a"
    assert resolved.recording_input.data_path == tmp_path / "test" / "a.bin"
    assert resolved.recording_input.data_format == "float16_interleaved_le"
    assert resolved.recording_input.sample_rate_hz == pytest.approx(SAMPLE_RATE_HZ)
    assert resolved.recording_input.center_frequency_hz == pytest.approx(2_416_000_000.0)
    assert resolved.recording_input.frequency_low_hz == pytest.approx(2_401_000_000.0)
    assert resolved.recording_input.frequency_high_hz == pytest.approx(2_431_000_000.0)
    assert resolved.recording_input.duration_s == pytest.approx(DURATION_S)
    assert resolved.recording_input.label_space == "spacenet_14"


# ---------------------------------------------------------------------------
# B. no GroundTruth leakage
# ---------------------------------------------------------------------------


def test_no_ground_truth_leakage(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    resolved = _resolve(
        tmp_path,
        expected_source_hash=hashlib.sha256(bin_path.read_bytes()).hexdigest(),
        expected_fingerprint=_expected_fingerprint(bin_path),
    )

    assert not hasattr(resolved, "signals")
    assert not hasattr(resolved, "ground_truth")
    assert not hasattr(resolved, "sample")
    assert not hasattr(resolved.recording_input, "signals")
    assert not hasattr(resolved.recording_input, "ground_truth")

    keys = set(vars(resolved.recording_input).keys())
    assert "signals" not in keys
    assert "ground_truth" not in keys
    assert "class_id" not in keys
    assert "class_name" not in keys
    assert "label_space" in keys


# ---------------------------------------------------------------------------
# C. raw source hash mismatch
# ---------------------------------------------------------------------------


def test_source_data_hash_mismatch(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    correct_fingerprint = _expected_fingerprint(bin_path)
    wrong_source = "f" * 64

    with pytest.raises(PlatformError) as exc:
        _resolve(
            tmp_path,
            expected_source_hash=wrong_source,
            expected_fingerprint=correct_fingerprint,
        )
    assert exc.value.code == "SOURCE_DATA_HASH_MISMATCH"


# ---------------------------------------------------------------------------
# D. recording fingerprint mismatch
# ---------------------------------------------------------------------------


def test_recording_fingerprint_mismatch(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    correct_source = hashlib.sha256(bin_path.read_bytes()).hexdigest()
    wrong_fingerprint = "e" * 64

    with pytest.raises(PlatformError) as exc:
        _resolve(
            tmp_path,
            expected_source_hash=correct_source,
            expected_fingerprint=wrong_fingerprint,
        )
    assert exc.value.code == "RECORDING_FINGERPRINT_MISMATCH"


# ---------------------------------------------------------------------------
# E. the two identities are independent
# ---------------------------------------------------------------------------


def test_identity_independence_iq_only_mutation(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    old_source = hashlib.sha256(bin_path.read_bytes()).hexdigest()
    old_fingerprint = _expected_fingerprint(bin_path)

    # Same byte length, same metadata JSON; only IQ bytes change.
    other_bytes = _bin_bytes_variant()
    assert len(other_bytes) == bin_path.stat().st_size
    assert other_bytes != bin_path.read_bytes()
    bin_path.write_bytes(other_bytes)

    new_source = hashlib.sha256(bin_path.read_bytes()).hexdigest()
    assert new_source != old_source
    # Semantic fingerprint is unchanged because metadata/GT are identical.
    assert _expected_fingerprint(bin_path) == old_fingerprint

    with pytest.raises(PlatformError) as exc:
        _resolve(
            tmp_path,
            expected_source_hash=old_source,
            expected_fingerprint=old_fingerprint,
        )
    assert exc.value.code == "SOURCE_DATA_HASH_MISMATCH"


def test_identity_independence_gt_only_mutation(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    old_source = hashlib.sha256(bin_path.read_bytes()).hexdigest()
    old_fingerprint = _expected_fingerprint(bin_path)

    # Same IQ bytes; only GT semantic content changes (class 9 -> 2).
    bin_path, json_path = _write_sample(tmp_path, metadata=_metadata(class_id=2))

    assert hashlib.sha256(bin_path.read_bytes()).hexdigest() == old_source
    new_fingerprint = _expected_fingerprint(bin_path, class_id=2)
    assert new_fingerprint != old_fingerprint

    with pytest.raises(PlatformError) as exc:
        _resolve(
            tmp_path,
            expected_source_hash=old_source,
            expected_fingerprint=old_fingerprint,
        )
    assert exc.value.code == "RECORDING_FINGERPRINT_MISMATCH"


# ---------------------------------------------------------------------------
# F. unsafe keys rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_key", [
    "../a",
    "./a",
    "/a",
    "a/b",
    r"a\b",
    "C:\\tmp",
    "",
    "a b",
])
def test_unsafe_key_rejected(tmp_path: Path, bad_key):
    _write_sample(tmp_path)  # so default expected-identity computation succeeds
    with pytest.raises(PlatformError) as exc:
        _resolve(tmp_path, key=bad_key)
    assert exc.value.code == "REMOTE_REQUEST_INVALID"


# ---------------------------------------------------------------------------
# G. split fails closed
# ---------------------------------------------------------------------------


def test_invalid_split_fails_closed(tmp_path: Path):
    _write_sample(tmp_path)
    with pytest.raises(PlatformError) as exc:
        resolve_space_net(
            dataset_root=tmp_path,
            split="validation",
            key="a",
            label_space="spacenet_14",
            expected_fingerprint="0" * 64,
            expected_source_hash="0" * 64,
            label_space_root=LABEL_ROOT,
        )
    assert exc.value.code == "SPACENET_SPLIT_INVALID"


# ---------------------------------------------------------------------------
# H. missing sample
# ---------------------------------------------------------------------------


def test_missing_sample(tmp_path: Path):
    with pytest.raises(PlatformError) as exc:
        _resolve(tmp_path, key="missing")
    assert exc.value.code == "SPACENET_SAMPLE_NOT_FOUND"
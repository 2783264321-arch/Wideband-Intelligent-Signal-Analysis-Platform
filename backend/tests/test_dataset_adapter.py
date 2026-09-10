"""TASK C1 — DatasetAdapter registry + SpaceNet adapter.

The adapter seam is the platform boundary between dataset-specific layout /
GroundTruth and the inference-facing ``RecordingInput``. These tests pin the
double-identity contract (semantic fingerprint + exact raw-IQ byte hash), the
GroundTruth-isolation invariant, adapter selection by ``dataset_name``, and the
trusted/platform-owned dataset-root construction dependency.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
import pytest

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.core.errors import PlatformError
from app.datasets.adapter import (
    DatasetAdapter,
    DatasetAdapterRegistry,
    ResolvedRecordingInput,
    SpaceNetDatasetAdapter,
    create_dataset_adapter_registry,
)
from app.imported_runs.fingerprint import build_recording_fingerprint
from app.pipelines.base import RecordingInput

LABEL_ROOT = Path(__file__).resolve().parents[2] / "label_spaces"
OBS_LOW_MHZ = 2401.0
OBS_HIGH_MHZ = 2431.0
SAMPLE_RATE_HZ = (OBS_HIGH_MHZ - OBS_LOW_MHZ) * 1e6
NUM_SAMPLES = 3000
DURATION_S = NUM_SAMPLES / SAMPLE_RATE_HZ


def _bin_bytes() -> bytes:
    values = np.arange(NUM_SAMPLES * 2, dtype=np.float32) * 0.001
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
    json_path.write_text(
        json.dumps(metadata if metadata is not None else _metadata()), encoding="utf-8"
    )
    return bin_path, json_path


def _load_sample(bin_path: Path):
    from app.datasets.spacenet import SpaceNetAdapter

    root = bin_path.parent.parent
    return SpaceNetAdapter(root, LABEL_ROOT, label_space_id="spacenet_14").load("test", bin_path.stem)


def _expected_fingerprint(
    bin_path: Path, *, class_id: int = 9, label_space: str = "spacenet_14"
) -> str:
    sample = _load_sample(bin_path)
    ground_truth = tuple(
        ManifestGroundTruth(
            t_start_s=signal.t_start_s,
            t_end_s=signal.t_end_s,
            f_low_hz=signal.f_low_hz,
            f_high_hz=signal.f_high_hz,
            class_id=signal.class_id,
            class_name=signal.class_name,
        )
        for signal in sample.signals
    )
    recording = ManifestRecording(
        recording_id="local-dummy",
        name=sample.id,
        data_format=sample.data_format,
        sample_rate_hz=sample.sample_rate_hz,
        center_frequency_hz=sample.center_frequency_hz,
        frequency_low_hz=sample.frequency_low_hz,
        frequency_high_hz=sample.frequency_high_hz,
        num_samples=sample.num_samples,
        duration_s=sample.duration_s,
        ground_truth=ground_truth,
    )
    return build_recording_fingerprint("SpaceNet", "test", label_space, recording).sha256


def _adapter(root: Path) -> SpaceNetDatasetAdapter:
    return SpaceNetDatasetAdapter(root)


def _resolve(
    adapter: SpaceNetDatasetAdapter,
    tmp_path: Path,
    *,
    key: str = "a",
    expected_fingerprint: str | None = None,
    expected_source_hash: str | None = None,
    label_space: str = "spacenet_14",
):
    return adapter.resolve(
        split="test",
        key=key,
        label_space=label_space,
        expected_fingerprint=expected_fingerprint
        or _expected_fingerprint(tmp_path / "test" / "a.bin"),
        expected_source_hash=expected_source_hash
        or hashlib.sha256(_bin_bytes()).hexdigest(),
        label_space_root=LABEL_ROOT,
    )


# ---------------------------------------------------------------------------
# A. SpaceNet adapter resolves and verifies the double identity
# ---------------------------------------------------------------------------


def test_spacenet_adapter_resolves_and_verifies(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    source = hashlib.sha256(bin_path.read_bytes()).hexdigest()
    fingerprint = _expected_fingerprint(bin_path)

    resolved = _resolve(
        _adapter(tmp_path),
        tmp_path,
        expected_fingerprint=fingerprint,
        expected_source_hash=source,
    )

    assert isinstance(resolved, ResolvedRecordingInput)
    assert resolved.recording_fingerprint == fingerprint
    assert resolved.source_data_sha256 == source
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
# B. GroundTruth isolation
# ---------------------------------------------------------------------------


def test_ground_truth_not_on_recording_input(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    resolved = _resolve(
        _adapter(tmp_path),
        tmp_path,
        expected_fingerprint=_expected_fingerprint(bin_path),
        expected_source_hash=hashlib.sha256(bin_path.read_bytes()).hexdigest(),
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
# C. identity mismatches fail closed
# ---------------------------------------------------------------------------


def test_fingerprint_mismatch_raises(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    with pytest.raises(PlatformError) as exc:
        _resolve(
            _adapter(tmp_path),
            tmp_path,
            expected_fingerprint="e" * 64,
            expected_source_hash=hashlib.sha256(bin_path.read_bytes()).hexdigest(),
        )
    assert exc.value.code == "RECORDING_FINGERPRINT_MISMATCH"


def test_source_hash_mismatch_raises(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    with pytest.raises(PlatformError) as exc:
        _resolve(
            _adapter(tmp_path),
            tmp_path,
            expected_fingerprint=_expected_fingerprint(bin_path),
            expected_source_hash="f" * 64,
        )
    assert exc.value.code == "SOURCE_DATA_HASH_MISMATCH"


def test_identity_uses_recording_label_space(tmp_path: Path):
    bin_path, _ = _write_sample(tmp_path)
    adapter = _adapter(tmp_path)
    source = hashlib.sha256(bin_path.read_bytes()).hexdigest()

    # A fingerprint built with the dataset label space verifies.
    _resolve(
        adapter,
        tmp_path,
        expected_fingerprint=_expected_fingerprint(bin_path, label_space="spacenet_14"),
        expected_source_hash=source,
    )

    # A fingerprint built with a *different* label space (e.g. a plugin output
    # label space) is not the recording identity and must fail closed.
    other_space = _expected_fingerprint(bin_path, label_space="plugin_output_ls")
    with pytest.raises(PlatformError) as exc:
        _resolve(
            adapter,
            tmp_path,
            expected_fingerprint=other_space,
            expected_source_hash=source,
        )
    assert exc.value.code == "RECORDING_FINGERPRINT_MISMATCH"


# ---------------------------------------------------------------------------
# D. registry selection
# ---------------------------------------------------------------------------


def test_unknown_dataset_raises_adapter_not_found():
    registry = create_dataset_adapter_registry()
    with pytest.raises(PlatformError) as exc:
        registry.get("UnknownDataset")
    assert exc.value.code == "DATASET_ADAPTER_NOT_FOUND"


def test_registry_selects_adapter_by_dataset_name(tmp_path: Path):
    adapter = _adapter(tmp_path)
    registry = create_dataset_adapter_registry([adapter])
    assert registry.get("SpaceNet") is adapter
    assert isinstance(registry.get("SpaceNet"), DatasetAdapter)


def test_registry_register_is_idempotent_last_wins(tmp_path: Path):
    first = _adapter(tmp_path)
    second = _adapter(tmp_path / "other")
    registry = DatasetAdapterRegistry([first])
    registry.register(second)
    assert registry.get("SpaceNet") is second


# ---------------------------------------------------------------------------
# E. trusted key + split safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_key", ["../a", "./a", "/a", "a/b", r"a\b", "C:\\tmp", "", "a b"]
)
def test_unsafe_key_rejected(tmp_path: Path, bad_key):
    _write_sample(tmp_path)
    with pytest.raises(PlatformError) as exc:
        _resolve(_adapter(tmp_path), tmp_path, key=bad_key)
    assert exc.value.code == "REMOTE_REQUEST_INVALID"


def test_invalid_split_fails_closed(tmp_path: Path):
    _write_sample(tmp_path)
    with pytest.raises(PlatformError) as exc:
        _adapter(tmp_path).resolve(
            split="validation",
            key="a",
            label_space="spacenet_14",
            expected_fingerprint="0" * 64,
            expected_source_hash="0" * 64,
            label_space_root=LABEL_ROOT,
        )
    assert exc.value.code == "SPACENET_SPLIT_INVALID"


# ---------------------------------------------------------------------------
# F. dataset root is a trusted construction dependency, never wire-controlled
# ---------------------------------------------------------------------------


def test_dataset_root_is_construction_dependency_not_wire(tmp_path: Path):
    adapter = _adapter(tmp_path)
    assert adapter.root == tmp_path
    assert adapter.dataset_name == "SpaceNet"
    # The dataset root must not be expressible as a per-request/wire argument.
    assert "dataset_root" not in inspect.signature(SpaceNetDatasetAdapter.resolve).parameters

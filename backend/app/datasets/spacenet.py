from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from app.core.errors import PlatformError
from app.labels.service import LabelSpaceService


@dataclass(frozen=True)
class SpaceNetSignal:
    id: str
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str


@dataclass(frozen=True)
class SpaceNetSample:
    id: str
    split: str
    data_path: Path
    metadata_path: Path
    data_format: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    num_samples: int
    duration_s: float
    signals: tuple[SpaceNetSignal, ...]


class SpaceNetAdapter:
    """Parse the verified SpaceNet advanced ``.bin + .json`` contract."""

    _splits = frozenset({"train", "test"})

    def __init__(self, root: Path, label_space_root: Path, label_space_id: str = "spacenet_14"):
        self.root = Path(root)
        self.labels = LabelSpaceService(Path(label_space_root))
        self.label_space_id = label_space_id

    def list_samples(self, split: str) -> list[SpaceNetSample]:
        split_root = self._split_root(split)
        if not split_root.is_dir():
            raise PlatformError("SPACENET_SPLIT_NOT_FOUND", f"SpaceNet split '{split}' was not found.", 404)
        bin_stems = {path.stem for path in split_root.glob("*.bin")}
        json_stems = {path.stem for path in split_root.glob("*.json")}
        if bin_stems != json_stems:
            missing_bin = sorted(json_stems - bin_stems)
            missing_json = sorted(bin_stems - json_stems)
            raise PlatformError(
                "INVALID_SPACENET_SAMPLE",
                "SpaceNet .bin and .json files must be paired by stem.",
                details={"missing_bin": missing_bin, "missing_json": missing_json},
            )
        return [self.load(split, stem) for stem in sorted(bin_stems)]

    def load(self, split: str, sample_id: str) -> SpaceNetSample:
        split_root = self._split_root(split)
        if not sample_id or Path(sample_id).name != sample_id:
            self._invalid("Sample id must be a single file stem.")
        data_path = split_root / f"{sample_id}.bin"
        metadata_path = split_root / f"{sample_id}.json"
        if not data_path.is_file() or not metadata_path.is_file():
            raise PlatformError("SPACENET_SAMPLE_NOT_FOUND", f"SpaceNet sample '{split}/{sample_id}' was not found.", 404)

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self._invalid(f"Unable to read SpaceNet metadata: {error}")
        if not isinstance(metadata, dict):
            self._invalid("SpaceNet metadata must be a JSON object.")

        observation_range = metadata.get("observation_range")
        if not isinstance(observation_range, list) or len(observation_range) != 2:
            self._invalid("observation_range must contain [low_mhz, high_mhz].")
        frequency_low_mhz, frequency_high_mhz = self._finite_pair(observation_range, "observation_range")
        if frequency_low_mhz >= frequency_high_mhz:
            self._invalid("observation_range low must be less than high.")

        byte_size = data_path.stat().st_size
        if byte_size == 0 or byte_size % 4:
            self._invalid("SpaceNet IQ must contain non-empty little-endian float16 I/Q pairs.")
        num_samples = byte_size // 4
        sample_rate_hz = (frequency_high_mhz - frequency_low_mhz) * 1e6
        duration_s = num_samples / sample_rate_hz
        label_space = self.labels.get(self.label_space_id)
        class_names = {item.id: item.name for item in label_space.classes}

        raw_signals = metadata.get("signals", [])
        if not isinstance(raw_signals, list):
            self._invalid("signals must be a JSON array.")
        signals: list[SpaceNetSignal] = []
        for index, raw_signal in enumerate(raw_signals):
            if not isinstance(raw_signal, dict):
                self._invalid(f"signals[{index}] must be a JSON object.")
            start_frequency_mhz, end_frequency_mhz = self._finite_pair(
                [raw_signal.get("start_frequency"), raw_signal.get("end_frequency")],
                f"signals[{index}] frequency",
            )
            start_time_ms, end_time_ms = self._finite_pair(
                [raw_signal.get("start_time"), raw_signal.get("end_time")],
                f"signals[{index}] time",
            )
            class_id = raw_signal.get("class")
            if isinstance(class_id, bool) or not isinstance(class_id, int) or class_id not in class_names:
                self._invalid(f"signals[{index}] class must be a valid {self.label_space_id} id.")
            t_start_s = start_time_ms / 1000.0
            t_end_s = end_time_ms / 1000.0
            f_low_hz = start_frequency_mhz * 1e6
            f_high_hz = end_frequency_mhz * 1e6
            if not (0.0 <= t_start_s < t_end_s <= duration_s + 1e-12):
                self._invalid(f"signals[{index}] time bounds fall outside the sample duration.")
            if not (frequency_low_mhz * 1e6 <= f_low_hz < f_high_hz <= frequency_high_mhz * 1e6):
                self._invalid(f"signals[{index}] frequency bounds fall outside observation_range.")
            signals.append(
                SpaceNetSignal(
                    id=str(raw_signal.get("signal_id", index)),
                    t_start_s=t_start_s,
                    t_end_s=t_end_s,
                    f_low_hz=f_low_hz,
                    f_high_hz=f_high_hz,
                    class_id=class_id,
                    class_name=class_names[class_id],
                )
            )

        return SpaceNetSample(
            id=sample_id,
            split=split,
            data_path=data_path,
            metadata_path=metadata_path,
            data_format="float16_interleaved_le",
            sample_rate_hz=sample_rate_hz,
            center_frequency_hz=((frequency_low_mhz + frequency_high_mhz) / 2.0) * 1e6,
            frequency_low_hz=frequency_low_mhz * 1e6,
            frequency_high_hz=frequency_high_mhz * 1e6,
            num_samples=num_samples,
            duration_s=duration_s,
            signals=tuple(signals),
        )

    def read_iq(self, sample: SpaceNetSample, start_sample: int = 0, count: int | None = None) -> np.ndarray:
        if start_sample < 0 or start_sample > sample.num_samples:
            self._invalid("start_sample is outside the SpaceNet sample.")
        if count is not None and count < 0:
            self._invalid("count must be non-negative.")
        end_sample = sample.num_samples if count is None else start_sample + count
        if end_sample > sample.num_samples:
            self._invalid("Requested IQ segment is outside the SpaceNet sample.")
        values = np.memmap(sample.data_path, mode="r", dtype="<f2", shape=(sample.num_samples * 2,))
        try:
            segment = np.asarray(values[start_sample * 2 : end_sample * 2], dtype=np.float32).copy()
        finally:
            del values
        pairs = segment.reshape(-1, 2)
        return (pairs[:, 0] + 1j * pairs[:, 1]).astype(np.complex64, copy=False)

    def _split_root(self, split: str) -> Path:
        if split not in self._splits:
            raise PlatformError("SPACENET_SPLIT_INVALID", f"Unsupported SpaceNet split '{split}'.")
        return self.root / split

    @staticmethod
    def _finite_pair(value: Any, field_name: str) -> tuple[float, float]:
        if not isinstance(value, list) or len(value) != 2:
            SpaceNetAdapter._invalid(f"{field_name} must contain two numbers.")
        try:
            first, second = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            SpaceNetAdapter._invalid(f"{field_name} must contain two numbers.")
        if not math.isfinite(first) or not math.isfinite(second):
            SpaceNetAdapter._invalid(f"{field_name} must contain finite numbers.")
        return first, second

    @staticmethod
    def _invalid(message: str) -> None:
        raise PlatformError("INVALID_SPACENET_SAMPLE", message)

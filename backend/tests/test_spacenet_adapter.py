import json
from pathlib import Path

import numpy as np
import pytest

from app.core.errors import PlatformError
from app.datasets.spacenet import SpaceNetAdapter


LABEL_ROOT = Path(__file__).resolve().parents[2] / "label_spaces"


def _write_sample(root: Path, *, stem: str = "0", values: list[float] | None = None, metadata: dict | None = None) -> None:
    split_root = root / "train"
    split_root.mkdir(parents=True, exist_ok=True)
    np.asarray(values or [1.0, 2.0, 3.0, 4.0], dtype="<f2").tofile(split_root / f"{stem}.bin")
    payload = metadata or {
        "observation_range": [2401.0, 2431.0],
        "signals": [
            {
                "signal_id": 0,
                "start_frequency": 2417.97385,
                "end_frequency": 2418.02615,
                "start_time": 0.0,
                "end_time": 0.0000001,
                "class": 9,
            }
        ],
    }
    (split_root / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_loads_float16_interleaved_iq_and_converts_physical_metadata(tmp_path: Path):
    _write_sample(tmp_path)

    sample = SpaceNetAdapter(tmp_path, LABEL_ROOT).load("train", "0")

    assert sample.data_format == "float16_interleaved_le"
    assert sample.num_samples == 2
    assert sample.sample_rate_hz == 30_000_000.0
    assert sample.center_frequency_hz == 2_416_000_000.0
    assert sample.frequency_low_hz == 2_401_000_000.0
    assert sample.frequency_high_hz == 2_431_000_000.0
    assert sample.duration_s == pytest.approx(2 / 30_000_000)
    assert sample.signals[0].class_id == 9
    assert sample.signals[0].class_name == "LoRa 250kHz"
    assert sample.signals[0].t_start_s == 0.0
    assert sample.signals[0].f_low_hz == pytest.approx(2_417_973_850.0)
    np.testing.assert_array_equal(SpaceNetAdapter(tmp_path, LABEL_ROOT).read_iq(sample), np.array([1 + 2j, 3 + 4j], dtype=np.complex64))


def test_allows_signal_at_recording_time_boundary(tmp_path: Path):
    _write_sample(
        tmp_path,
        metadata={
            "observation_range": [2401.0, 2431.0],
            "signals": [{
                "signal_id": 7,
                "start_frequency": 2401.0,
                "end_frequency": 2431.0,
                "start_time": 0.0,
                "end_time": 0.0000666666667,
                "class": 2,
            }],
        },
    )

    sample = SpaceNetAdapter(tmp_path, LABEL_ROOT).load("train", "0")

    assert sample.signals[0].t_end_s == pytest.approx(sample.duration_s)


@pytest.mark.parametrize(
    "metadata,values,code",
    [
        ({"observation_range": [2431.0, 2401.0], "signals": []}, [1.0, 2.0], "INVALID_SPACENET_SAMPLE"),
        ({"observation_range": [2401.0, 2431.0], "signals": [{"signal_id": 0, "start_frequency": 2400.0, "end_frequency": 2402.0, "start_time": 0.0, "end_time": 0.00000001, "class": 9}]}, [1.0, 2.0], "INVALID_SPACENET_SAMPLE"),
    ],
)
def test_rejects_invalid_observation_or_signal_bounds(tmp_path: Path, metadata: dict, values: list[float], code: str):
    _write_sample(tmp_path, values=values, metadata=metadata)

    with pytest.raises(PlatformError) as error:
        SpaceNetAdapter(tmp_path, LABEL_ROOT).load("train", "0")

    assert error.value.code == code


def test_rejects_odd_float16_interleaving(tmp_path: Path):
    _write_sample(tmp_path, values=[1.0, 2.0, 3.0])

    with pytest.raises(PlatformError) as error:
        SpaceNetAdapter(tmp_path, LABEL_ROOT).load("train", "0")

    assert error.value.code == "INVALID_SPACENET_SAMPLE"

"""TASK E1 — ZoomSpec plugin runtime factory (device-propagation gate).

This file currently contains the E1 pre-implementation device-propagation gate.
The frozen scientific pipeline must preserve an exact RuntimeDescriptor
``device_index`` across LS-STFT preprocessing, CPN, and FRN. If it cannot, E1 is
blocked pending an architect ruling (see E1_BLOCKED_BY_ZOOMSPEC_DEVICE_PROPAGATION).
No GPU / torch / ultralytics are used.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3 import pipeline as pipeline_module
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import _is_cuda_device


def _label_space():
    return SimpleNamespace(
        id="spacenet_14",
        classes=[SimpleNamespace(id=i, name=f"class_{i}") for i in range(14)],
    )


def _spy_pipeline(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeDetector:
        def __init__(self, checkpoint_path, *, device):
            captured["detector_device"] = device

        def detect_batch(self, spectrograms, *, batch_size):
            return [[] for _ in spectrograms]

    class _FakeFRN:
        def __init__(self, checkpoint_path, *, device):
            captured["frn_device"] = device

        def refine_batch(self, candidates, **kwargs):
            return []

    def _fake_build_ls_stft(iq, *, sample_rate_hz, center_frequency_hz,
                            frequency_low_hz, frequency_high_hz, normalization, device):
        captured["preprocess_device"] = device
        return SimpleNamespace()

    monkeypatch.setattr(pipeline_module, "CPNDetector", _FakeDetector)
    monkeypatch.setattr(pipeline_module, "FRNRefiner", _FakeFRN)
    monkeypatch.setattr(pipeline_module, "build_ls_stft_spectrogram", _fake_build_ls_stft)
    monkeypatch.setattr(pipeline_module, "read_segment_from_path", lambda path, fmt: np.zeros(8, dtype=np.complex64))
    return captured


def _recording():
    return SimpleNamespace(
        id="rec",
        data_path="ignored",
        data_format="float16_interleaved_le",
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2.4e9,
        frequency_low_hz=2.4e9,
        frequency_high_hz=2.41e9,
        duration_s=1.0,
        label_space="spacenet_14",
    )


def _run(monkeypatch, device):
    captured = _spy_pipeline(monkeypatch)
    pipeline = pipeline_module.ZoomSpecFrozenPipeline(
        detector_checkpoint_path="det",
        frn_checkpoint_path="frn",
        normalization=object(),
        label_space=_label_space(),
        device=device,
    )
    pipeline.run(_recording(), {}, __import__("pathlib").Path("/tmp/e1_ws"))
    return captured


def test_descriptor_index_3_propagates_exactly(monkeypatch):
    """INTENDED (RED): RuntimeDescriptor.device_index=3 must reach LS-STFT, CPN and
    FRN as CUDA device 3 exactly."""
    captured = _run(monkeypatch, 3)
    assert captured["detector_device"] == 3
    assert captured["frn_device"] == 3
    # This assertion fails on the current frozen pipeline: int 3 is coerced to the
    # generic string "cuda" (device 0) for LS-STFT preprocessing.
    assert captured["preprocess_device"] == "cuda:3"


def test_int_index_3_characterization_preprocessing_targets_device_3(monkeypatch):
    """Characterization: with int 3, CPN/FRN receive 3 and LS-STFT receives the
    explicit ``"cuda:3"`` device string."""
    captured = _run(monkeypatch, 3)
    assert captured["detector_device"] == 3
    assert captured["frn_device"] == 3
    assert captured["preprocess_device"] == "cuda:3"


def test_ls_stft_device_mapping():
    """Device plumbing mapping: int 0 -> 'cuda'; int N>0 -> 'cuda:N'; strings
    preserved unchanged. Non-scientific."""
    assert pipeline_module._ls_stft_device(0) == "cuda"
    assert pipeline_module._ls_stft_device(3) == "cuda:3"
    assert pipeline_module._ls_stft_device("cuda") == "cuda"
    assert pipeline_module._ls_stft_device("cuda:3") == "cuda:3"
    assert pipeline_module._ls_stft_device("cpu") == "cpu"


def test_int_frn_device_is_cuda():
    """Existing FRN int-device CUDA/autocast semantics are untouched."""
    assert _is_cuda_device(0) is True
    assert _is_cuda_device(3) is True


def test_int_index_0_preserves_accepted_m91_semantics(monkeypatch):
    """Index 0 already works: LS-STFT "cuda" == torch device 0, and CPN/FRN get 0."""
    captured = _run(monkeypatch, 0)
    assert captured["preprocess_device"] == "cuda"
    assert captured["detector_device"] == 0
    assert captured["frn_device"] == 0
    assert _is_cuda_device(0) is True

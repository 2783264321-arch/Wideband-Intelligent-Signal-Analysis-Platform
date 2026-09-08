"""M9.1 Task 12D — frozen Combined FRN V3 production port.

Ports the historical frozen ``ZoomSpecFRN`` Combined V3 stage into a platform
native implementation:

    PurifiedCandidate
    -> exact FRN feature construction (NumPy)
    -> frozen Combined FRN V3 model (portable PyTorch architecture)
    -> historical CUDA/float16-autocast forward
    -> normalized FRN prediction decode
    -> physical temporal / frequency refinement
    -> geometric confidence fusion
    -> FRNRefinedDetection

Independence contract:
- Pure-NumPy feature helpers and public dataclasses are importable WITHOUT
  Torch (so the platform ``.venv`` can import the module).
- Torch is imported lazily only inside ``FRNRefiner.__init__`` / forward.
- No legacy ZoomSpec imports, no historical absolute paths, no subprocess, no
  sys.path mutation.
- NMS, score-threshold filtering, DetectionPayload assembly are OUT of scope.

Frozen semantics are locked as internal constants; they are not caller options.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import PurifiedCandidate
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal

FRN_FEATURE_LENGTH = 4096
_FEATURE_EPSILON = 1e-8
_FRN_END_EPS = 1e-6
_BANDWIDTH_MIN_HZ = 1e3
_BANDWIDTH_MAX_HZ = 8e7

# Frozen model config (must match the checkpoint embedded config).
_FROZEN_CHANNELS = 128
_FROZEN_NUM_CLASSES = 14
_FROZEN_FUSION_ATTENTION = True
_FROZEN_USE_BANDWIDTH_CONTEXT = True
_FROZEN_USE_CENTER_REGRESSION = True
_FROZEN_USE_LOG_BANDWIDTH = False
_FROZEN_USE_ATTENTION_POOL = False
_FROZEN_INPUT_LENGTH = 4096


# ---------------------------------------------------------------------------
# Public platform types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FRNPrediction:
    class_id: int
    signal_probability: float
    class_probability: float
    start_norm: float
    duration_norm: float
    bandwidth_norm: float
    center_offset_norm: float


@dataclass(frozen=True)
class FRNRefinedDetection:
    proposal: CPNProposal
    prediction: FRNPrediction
    class_id: int
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    confidence: float


@dataclass(frozen=True)
class _FRNRawHeads:
    class_logits: object
    signal_logit: object
    start_logits: object
    duration_logits: object
    bandwidth_logits: object
    center_offset: object
    start: object
    duration: object
    bandwidth: object


def validate_batch_size(batch_size: int) -> None:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")


# ---------------------------------------------------------------------------
# Pure-NumPy feature construction
# ---------------------------------------------------------------------------


def bandwidth_context(lowpass_hz: float) -> float:
    return float(np.log2(max(lowpass_hz, 1e-6) / 1e6))


def _resample_rows(values: np.ndarray, output_length: int) -> np.ndarray:
    if values.ndim != 2 or values.shape[1] < 1:
        raise ValueError("values must have shape [channels, length]")
    if output_length < 2:
        raise ValueError("output_length must be at least two")
    if values.shape[1] == 1:
        return np.repeat(values, output_length, axis=1).astype(np.float32)
    source = np.linspace(0.0, 1.0, values.shape[1])
    target = np.linspace(0.0, 1.0, output_length)
    output = np.empty((values.shape[0], output_length), dtype=np.float32)
    for channel in range(values.shape[0]):
        output[channel] = np.interp(target, source, values[channel])
    return output


def build_frn_features(
    candidate: PurifiedCandidate,
) -> tuple[np.ndarray, np.ndarray, float, dict]:
    """Return (iq, fft, bandwidth_context, diagnostics).

    Reproduces the frozen historical ``global_resample`` FRN feature path.
    """
    signal = candidate.iq.astype(np.complex64, copy=False)
    rms = float(np.sqrt(np.mean(np.abs(signal) ** 2)))
    normalized = signal / max(rms, 1e-8)
    iq_rows = np.stack((normalized.real, normalized.imag)).astype(np.float32)
    iq = _resample_rows(iq_rows, FRN_FEATURE_LENGTH)

    spectrum = np.fft.fftshift(np.fft.fft(normalized)) / np.sqrt(normalized.size)
    log_magnitude = np.log1p(np.abs(spectrum)).astype(np.float32)
    fft_mean = float(log_magnitude.mean())
    fft_std = float(log_magnitude.std())
    standardized = (log_magnitude - fft_mean) / max(fft_std, 1e-8)
    fft = _resample_rows(standardized[None, :], FRN_FEATURE_LENGTH)

    if not np.isfinite(iq).all() or not np.isfinite(fft).all():
        raise ValueError("FRN features contain NaN or Inf")

    bandwidth = bandwidth_context(candidate.lowpass_hz)
    diagnostics = {
        "original_length": int(signal.size),
        "output_length": FRN_FEATURE_LENGTH,
        "input_rms": rms,
        "fft_log_mean": fft_mean,
        "fft_log_std": fft_std,
    }
    return iq, fft, bandwidth, diagnostics


# ---------------------------------------------------------------------------
# Decode helpers
# ---------------------------------------------------------------------------


def decode_classification(
    class_logits: np.ndarray,
    signal_logit: np.ndarray,
) -> tuple[int, float, float]:
    probs = np.exp(class_logits - np.max(class_logits, axis=-1, keepdims=True))
    probs = probs / probs.sum(axis=-1, keepdims=True)
    class_id = int(np.argmax(class_logits))
    class_probability = float(probs[class_id])
    signal_probability = float(1.0 / (1.0 + np.exp(-float(signal_logit))))
    return class_id, class_probability, signal_probability


def regression_expectation(logits: np.ndarray) -> np.ndarray:
    logits = np.asarray(logits)
    grid = np.linspace(0.0, 1.0, logits.shape[-1], dtype=np.float64)
    logits_f = logits.astype(np.float64)
    logits_f = logits_f - np.max(logits_f, axis=-1, keepdims=True)
    exp = np.exp(logits_f)
    probs = exp / exp.sum(axis=-1, keepdims=True)
    return np.sum(probs * grid, axis=-1).astype(np.float32)


def decode_temporal_box(
    candidate: PurifiedCandidate,
    *,
    start_norm: float,
    duration_norm: float,
    recording_sample_rate_hz: float,
    recording_duration_s: float,
) -> tuple[float, float]:
    crop_duration = candidate.crop_t_end_s - candidate.crop_t_start_s
    t0 = candidate.crop_t_start_s + float(start_norm) * crop_duration
    end_norm = min(1.0 - _FRN_END_EPS, float(start_norm) + float(duration_norm))
    t1 = candidate.crop_t_start_s + end_norm * crop_duration
    minimum_duration = 1.0 / recording_sample_rate_hz
    t0 = float(np.clip(t0, 0.0, max(recording_duration_s - minimum_duration, 0.0)))
    t1 = float(min(recording_duration_s, max(t1, t0 + minimum_duration)))
    return t0, t1


def decode_frequency_box(
    candidate: PurifiedCandidate,
    *,
    bandwidth_norm: float,
    center_offset_norm: float,
    frequency_low_hz: float,
    frequency_high_hz: float,
) -> tuple[float, float]:
    bandwidth_hz = max(float(bandwidth_norm) * 2.0 * candidate.lowpass_hz, 1.0)
    proposal_center_hz = 0.5 * (candidate.proposal.f_low_hz + candidate.proposal.f_high_hz)
    center_hz = proposal_center_hz + float(center_offset_norm) * 2.0 * candidate.lowpass_hz
    f_low = max(frequency_low_hz, center_hz - 0.5 * bandwidth_hz)
    f_high = min(frequency_high_hz, center_hz + 0.5 * bandwidth_hz)
    return float(f_low), float(f_high)


def geometric_fusion(
    proposal_score: float,
    signal_probability: float,
    class_probability: float,
) -> float:
    return float(math.sqrt(max(proposal_score * signal_probability * class_probability, 0.0)))


# ---------------------------------------------------------------------------
# Lazy Torch model architecture (import torch only inside FRNRefiner)
# ---------------------------------------------------------------------------


def _build_model_module():
    """Import torch and build the frozen ZoomSpecFRN architecture.

    Returns ``(model, torch)``. This is the ONLY place torch is imported, so
    importing ``frn`` alone does not require Torch.
    """
    import torch  # noqa: F401
    from torch import nn

    class ChannelLayerNorm(nn.Module):
        def __init__(self, channels: int):
            super().__init__()
            self.norm = nn.LayerNorm(channels)

        def forward(self, x):
            return self.norm(x.transpose(1, 2)).transpose(1, 2)

    class LinearAttentionBlock(nn.Module):
        def __init__(self, channels: int):
            super().__init__()
            self.query = nn.Conv1d(channels, 1, 1)
            self.key = nn.Conv1d(channels, channels, 1)
            self.global_projection = nn.Linear(channels, channels)
            self.output = nn.Conv1d(channels, channels, 1)
            self.norm = ChannelLayerNorm(channels)

        def forward(self, x):
            weights = torch.softmax(self.query(x), dim=-1)
            summary = torch.sum(x * weights, dim=-1)
            global_term = self.global_projection(summary).unsqueeze(-1)
            gated = torch.sigmoid(self.key(x) + global_term) * x
            return self.norm(x + self.output(gated))

    class DomainEncoder(nn.Module):
        def __init__(self, input_channels: int, channels: int = _FROZEN_CHANNELS):
            super().__init__()
            self.stem = nn.Sequential(
                nn.Conv1d(input_channels, input_channels, 9, stride=4, padding=4, groups=input_channels),
                nn.Conv1d(input_channels, channels, 1),
                ChannelLayerNorm(channels),
                nn.GELU(),
            )
            self.lstm = nn.LSTM(channels, channels // 2, num_layers=1, bidirectional=True, batch_first=True)
            self.local = nn.Sequential(
                nn.Conv1d(channels, channels, 7, padding=3, groups=channels),
                nn.Conv1d(channels, channels, 1),
                nn.GELU(),
                ChannelLayerNorm(channels),
            )
            self.attention = nn.Sequential(
                LinearAttentionBlock(channels),
                LinearAttentionBlock(channels),
            )

        def forward(self, x):
            x = self.stem(x)
            sequence, _ = self.lstm(x.transpose(1, 2))
            x = sequence.transpose(1, 2)
            x = x + self.local(x)
            return self.attention(x)

    class ZoomSpecFRN(nn.Module):
        def __init__(
            self,
            channels: int = _FROZEN_CHANNELS,
            num_classes: int = _FROZEN_NUM_CLASSES,
            fusion_attention: bool = _FROZEN_FUSION_ATTENTION,
            use_bandwidth_context: bool = _FROZEN_USE_BANDWIDTH_CONTEXT,
            use_center_regression: bool = _FROZEN_USE_CENTER_REGRESSION,
            use_log_bandwidth: bool = _FROZEN_USE_LOG_BANDWIDTH,
            use_attention_pool: bool = _FROZEN_USE_ATTENTION_POOL,
        ):
            super().__init__()
            self.use_bandwidth_context = use_bandwidth_context
            self.use_center_regression = use_center_regression
            self.use_log_bandwidth = use_log_bandwidth
            self.use_attention_pool = use_attention_pool
            self.iq_encoder = DomainEncoder(2, channels)
            self.fft_encoder = DomainEncoder(1, channels)
            self.fusion = nn.Sequential(
                nn.Conv1d(2 * channels, 2 * channels, 1),
                ChannelLayerNorm(2 * channels),
                nn.GELU(),
                nn.Conv1d(2 * channels, channels, 1),
            )
            self.fusion_attention = LinearAttentionBlock(channels) if fusion_attention else nn.Identity()
            if use_attention_pool:
                self.pool_attn = nn.Conv1d(channels, 1, 1)
            self.start_head = nn.Conv1d(channels, 1, 1)
            self.duration_head = nn.Conv1d(channels, 1, 1)
            self.bandwidth_head = nn.Conv1d(channels, 1, 1)
            self.class_head = nn.Linear(channels + (1 if use_bandwidth_context else 0), num_classes)
            self.signal_head = nn.Linear(channels, 1)
            self.center_head = nn.Linear(channels, 1) if use_center_regression else None

        @staticmethod
        def _expectation(logits):
            grid = torch.linspace(0.0, 1.0, logits.shape[-1], device=logits.device, dtype=logits.dtype)
            return torch.sum(torch.softmax(logits, dim=-1) * grid, dim=-1)

        def forward(self, iq, fft, bw_context=None):
            iq_features = self.iq_encoder(iq)
            fft_features = self.fft_encoder(fft)
            fused = self.fusion_attention(self.fusion(torch.cat([iq_features, fft_features], dim=1)))
            start_logits = self.start_head(iq_features).squeeze(1)
            duration_logits = self.duration_head(iq_features).squeeze(1)
            bandwidth_logits = self.bandwidth_head(fft_features).squeeze(1)
            bandwidth_value = self._expectation(bandwidth_logits)
            pooled = fused.mean(dim=-1)
            class_input = pooled
            if bw_context is not None:
                if not self.use_bandwidth_context:
                    raise ValueError("bw_context passed to a model built without bandwidth context")
                class_input = torch.cat([pooled, bw_context.unsqueeze(-1)], dim=-1)
            center_offset = None
            if self.center_head is not None:
                center_offset = torch.tanh(self.center_head(pooled)).squeeze(-1)
            return {
                "class_logits": self.class_head(class_input),
                "signal_logit": self.signal_head(pooled).squeeze(-1),
                "start_logits": start_logits,
                "duration_logits": duration_logits,
                "bandwidth_logits": bandwidth_logits,
                "center_offset": center_offset,
                "start": self._expectation(start_logits),
                "duration": self._expectation(duration_logits),
                "bandwidth": bandwidth_value,
            }

    return ZoomSpecFRN, torch


# ---------------------------------------------------------------------------
# FRNRefiner public class
# ---------------------------------------------------------------------------


class FRNRefiner:
    def __init__(self, checkpoint_path: Path, *, device: int | str) -> None:
        import torch

        self._torch = torch
        self._device = _resolve_device(device, torch)
        model_cls, _ = _build_model_module()
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        cfg = checkpoint["config"]
        self._validate_embedded_config(cfg)
        self._model = model_cls(
            channels=int(cfg["model"]["channels"]),
            num_classes=int(cfg["data"]["classes"]),
            fusion_attention=bool(cfg["model"]["fusion_attention"]),
            use_bandwidth_context=bool(cfg["model"].get("use_bandwidth_context", False)),
            use_center_regression=bool(cfg["model"].get("use_center_regression", False)),
            use_log_bandwidth=bool(cfg["model"].get("use_log_bandwidth", False)),
            use_attention_pool=bool(cfg["model"].get("use_attention_pool", False)),
        )
        state = checkpoint["model"]
        missing, unexpected = self._model.load_state_dict(state, strict=True)
        if missing or unexpected:
            raise RuntimeError(f"FRN checkpoint state dict mismatch: missing={missing}, unexpected={unexpected}")
        self._model.to(self._device).eval()

    @staticmethod
    def _validate_embedded_config(cfg: dict) -> None:
        model_cfg = cfg["model"]
        data_cfg = cfg["data"]
        checks = {
            "channels": int(model_cfg["channels"]) == _FROZEN_CHANNELS,
            "fusion_attention": bool(model_cfg["fusion_attention"]) == _FROZEN_FUSION_ATTENTION,
            "use_bandwidth_context": bool(model_cfg.get("use_bandwidth_context", False)) == _FROZEN_USE_BANDWIDTH_CONTEXT,
            "use_center_regression": bool(model_cfg.get("use_center_regression", False)) == _FROZEN_USE_CENTER_REGRESSION,
            "use_log_bandwidth": bool(model_cfg.get("use_log_bandwidth", False)) == _FROZEN_USE_LOG_BANDWIDTH,
            "use_attention_pool": bool(model_cfg.get("use_attention_pool", False)) == _FROZEN_USE_ATTENTION_POOL,
            "input_length": int(data_cfg["input_length"]) == _FROZEN_INPUT_LENGTH,
            "num_classes": int(data_cfg["classes"]) == _FROZEN_NUM_CLASSES,
        }
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            raise RuntimeError(f"incompatible FRN checkpoint config: {failed}")

    def _forward_raw_batch(self, iq_tensor, fft_tensor, bw_tensor, *, is_cuda: bool):
        torch = self._torch
        with torch.inference_mode():
            with torch.autocast(
                device_type="cuda" if is_cuda else "cpu",
                dtype=torch.float16,
                enabled=is_cuda,
            ):
                return self._model(iq_tensor, fft_tensor, bw_tensor)

    def _decode_outputs_to_numpy(self, output: dict, chunk_size: int, is_cuda: bool):
        """Reproduce historical decode exactly.

        Historical (``pipeline.refine_proposals``):
            class_probabilities = torch.softmax(class_logits, dim=1).float().cpu().numpy()
            signal_probabilities = torch.sigmoid(signal_logit).float().cpu().numpy()
            starts = output["start"].float().cpu().numpy()
            ...
        Class logits are float16 under autocast, so softmax is computed in
        float16 and then cast to float32. Signal sigmoid likewise.
        """
        torch = self._torch
        class_logits = output["class_logits"]
        class_probability_matrix = torch.softmax(class_logits, dim=1).float().cpu().numpy()
        signal_probabilities = torch.sigmoid(output["signal_logit"]).float().cpu().numpy()
        starts = output["start"].float().cpu().numpy()
        durations = output["duration"].float().cpu().numpy()
        bandwidths = output["bandwidth"].float().cpu().numpy()
        if self._model.use_center_regression:
            center_offsets = output["center_offset"].float().cpu().numpy()
        else:
            center_offsets = np.zeros(chunk_size, dtype=np.float32)
        return (
            class_logits.float().cpu().numpy(),
            class_probability_matrix,
            signal_probabilities,
            starts,
            durations,
            bandwidths,
            center_offsets,
        )

    def predict_batch(
        self,
        candidates: Sequence[PurifiedCandidate],
        *,
        batch_size: int = 64,
    ) -> list[FRNPrediction]:
        validate_batch_size(batch_size)
        if len(candidates) == 0:
            return []
        torch = self._torch
        is_cuda = _is_cuda_device(self._device)
        predictions: list[FRNPrediction] = []
        for start in range(0, len(candidates), batch_size):
            chunk = list(candidates[start : start + batch_size])
            iq_rows = []
            fft_rows = []
            bws = []
            for cand in chunk:
                iq, fft, bw, _ = build_frn_features(cand)
                iq_rows.append(iq)
                fft_rows.append(fft)
                bws.append(np.float32(bw))
            iq_tensor = torch.from_numpy(np.stack(iq_rows)).to(self._device)
            fft_tensor = torch.from_numpy(np.stack(fft_rows)).to(self._device)
            bw_tensor = (
                torch.from_numpy(np.asarray(bws, dtype=np.float32)).to(self._device)
                if self._model.use_bandwidth_context
                else None
            )
            output = self._forward_raw_batch(iq_tensor, fft_tensor, bw_tensor, is_cuda=is_cuda)
            (
                _raw_class_logits,
                class_probability_matrix,
                signal_probabilities,
                starts,
                durations,
                bandwidths,
                center_offsets,
            ) = self._decode_outputs_to_numpy(output, len(chunk), is_cuda)

            if len(class_probability_matrix) != len(chunk):
                raise RuntimeError("FRN model output cardinality mismatch")

            for i in range(len(chunk)):
                class_id = int(np.argmax(class_probability_matrix[i]))
                class_probability = float(class_probability_matrix[i][class_id])
                signal_probability = float(signal_probabilities[i])
                start_norm = float(starts[i])
                duration_norm = float(durations[i])
                bandwidth_norm = float(bandwidths[i])
                center_offset_norm = float(center_offsets[i])
                predictions.append(
                    FRNPrediction(
                        class_id=class_id,
                        signal_probability=signal_probability,
                        class_probability=class_probability,
                        start_norm=start_norm,
                        duration_norm=duration_norm,
                        bandwidth_norm=bandwidth_norm,
                        center_offset_norm=center_offset_norm,
                    )
                )
        if len(predictions) != len(candidates):
            raise RuntimeError("FRN prediction count mismatch")
        return predictions

    def refine_batch(
        self,
        candidates: Sequence[PurifiedCandidate],
        *,
        recording_sample_rate_hz: float,
        recording_duration_s: float,
        frequency_low_hz: float,
        frequency_high_hz: float,
        batch_size: int = 64,
    ) -> list[FRNRefinedDetection | None]:
        validate_batch_size(batch_size)
        if len(candidates) == 0:
            return []
        predictions = self.predict_batch(candidates, batch_size=batch_size)
        results: list[FRNRefinedDetection | None] = []
        for cand, pred in zip(candidates, predictions):
            t0, t1 = decode_temporal_box(
                cand,
                start_norm=pred.start_norm,
                duration_norm=pred.duration_norm,
                recording_sample_rate_hz=recording_sample_rate_hz,
                recording_duration_s=recording_duration_s,
            )
            f0, f1 = decode_frequency_box(
                cand,
                bandwidth_norm=pred.bandwidth_norm,
                center_offset_norm=pred.center_offset_norm,
                frequency_low_hz=frequency_low_hz,
                frequency_high_hz=frequency_high_hz,
            )
            if f1 <= f0:
                results.append(None)
                continue
            conf = geometric_fusion(
                cand.proposal.confidence,
                pred.signal_probability,
                pred.class_probability,
            )
            results.append(
                FRNRefinedDetection(
                    proposal=cand.proposal,
                    prediction=pred,
                    class_id=pred.class_id,
                    t_start_s=t0,
                    t_end_s=t1,
                    f_low_hz=f0,
                    f_high_hz=f1,
                    confidence=conf,
                )
            )
        return results


def _resolve_device(device: int | str, torch) -> str | int:
    if isinstance(device, int):
        return device
    d = str(device).lower()
    if d in ("cuda", "gpu", "cuda:0"):
        return "cuda"
    if d == "cpu":
        return "cpu"
    return device


def _is_cuda_device(device: int | str) -> bool:
    return device == "cuda" or (isinstance(device, int) and device >= 0)

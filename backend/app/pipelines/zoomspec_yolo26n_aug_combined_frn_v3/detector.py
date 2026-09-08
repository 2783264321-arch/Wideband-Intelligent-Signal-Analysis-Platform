"""Platform-native frozen CPN detector (Task 12B).

Reproduces the historical frozen ZoomSpec CPN detector stage
(``evaluate_cpn.py`` + ``coordinates.py``) as an independent production
implementation:

    LSSTFTSpectrogram -> frozen YOLOv26n CPN -> normalized boxes ->
    exact historical pixel-edge geometry conversion -> physical CPNProposal.

Detector classes are bandwidth tiers (0=narrow, 1=mid, 2=wide), NOT the final
14-class SpaceNet signal labels.

Independence contract:
- Torch / Ultralytics are imported lazily inside the detector execution path,
  so importing this module succeeds in environments without the ML stack
  (e.g. ``repo/.venv``).
- The model is loaded once per ``CPNDetector`` instance.
- No historical deployment paths, no legacy ZoomSpec package imports, no
  interpreter search-path mutation, no temporary PNG/PIL/OpenCV serialization,
  no NumPy detector fallback, no custom NMS.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
    LSSTFTSpectrogram,
    SpectrogramGeometry,
)

# Frozen historical inference parameters.
_CONF = 0.003
_NMS_IOU = 0.7
_MAX_DET = 300
_IMGSZ = 640


@dataclass(frozen=True)
class CPNRawDetection:
    normalized_xyxy: tuple[float, float, float, float]
    confidence: float
    bandwidth_tier: int


@dataclass(frozen=True)
class CPNProposal:
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    bandwidth_tier: int
    confidence: float


def _validate_image(image: np.ndarray) -> None:
    if image.shape != (640, 640):
        raise ValueError(f"CPN detector expects a (640, 640) image, got {image.shape}")
    if image.dtype != np.uint8:
        raise ValueError(f"CPN detector expects uint8 image, got {image.dtype}")


def _grid_edges(grid: np.ndarray, extent: tuple[float, float] | None = None) -> np.ndarray:
    if grid.ndim != 1 or grid.size < 2 or not np.all(np.diff(grid) > 0):
        raise ValueError("grid must be one-dimensional and strictly increasing")
    edges = np.empty(grid.size + 1, dtype=np.float64)
    edges[1:-1] = 0.5 * (grid[:-1] + grid[1:])
    edges[0] = grid[0] - 0.5 * (grid[1] - grid[0])
    edges[-1] = grid[-1] + 0.5 * (grid[-1] - grid[-2])
    if extent is not None:
        edges[0], edges[-1] = extent
    if not np.all(np.diff(edges) > 0):
        raise ValueError("inferred pixel edges are not strictly increasing")
    return edges


def _normalized_to_value(
    coord: float, grid: np.ndarray, extent: tuple[float, float] | None = None
) -> float:
    if not 0.0 <= coord <= 1.0:
        raise ValueError(f"normalized coordinate outside [0,1]: {coord}")
    edges = _grid_edges(grid, extent)
    edge_index = np.clip(coord * grid.size, 0.0, grid.size)
    return float(np.interp(edge_index, np.arange(edges.size, dtype=np.float64), edges))


def _clip_and_reject(
    box: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    clipped = np.clip(np.asarray(box, dtype=float), 0.0, 1.0)
    x0, y0, x1, y1 = (float(v) for v in clipped)
    if x0 >= x1 or y0 >= y1:
        return None
    return (x0, y0, x1, y1)


def image_box_to_cpn_proposal(
    box: tuple[float, float, float, float],
    geometry: SpectrogramGeometry,
    *,
    bandwidth_tier: int,
    confidence: float,
) -> CPNProposal:
    """Convert a normalized y-down image box into a physical CPNProposal.

    Reproduces the historical ``image_box_to_proposal(image_y_down=True)``
    pixel-edge mapping using the platform ``SpectrogramGeometry``.
    """
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"invalid normalized image box: {box}")
    t_start_s = _normalized_to_value(x0, geometry.time_grid_s, geometry.time_extent_s)
    t_end_s = _normalized_to_value(x1, geometry.time_grid_s, geometry.time_extent_s)
    f_low_hz = _normalized_to_value(1.0 - y1, geometry.frequency_grid_hz, geometry.frequency_extent_hz)
    f_high_hz = _normalized_to_value(1.0 - y0, geometry.frequency_grid_hz, geometry.frequency_extent_hz)
    return CPNProposal(
        t_start_s=t_start_s,
        t_end_s=t_end_s,
        f_low_hz=f_low_hz,
        f_high_hz=f_high_hz,
        bandwidth_tier=bandwidth_tier,
        confidence=confidence,
    )


def _detect_raw_batch(
    detector: "CPNDetector",
    spectrograms: Sequence[LSSTFTSpectrogram],
    *,
    batch_size: int,
) -> list[list[CPNRawDetection]]:
    """Raw detection without physical conversion; never exposed publicly."""
    images = [spec.image for spec in spectrograms]
    for img in images:
        _validate_image(img)

    model = detector._model
    results_all: list[list[CPNRawDetection]] = []
    for start in range(0, len(images), batch_size):
        chunk = images[start:start + batch_size]
        results = model.predict(
            source=chunk,
            conf=_CONF,
            iou=_NMS_IOU,
            max_det=_MAX_DET,
            imgsz=_IMGSZ,
            batch=len(chunk),
            device=detector._device,
            stream=False,
            verbose=False,
        )
        for result in results:
            batch_raw: list[CPNRawDetection] = []
            if result.boxes is not None:
                xyxy = result.boxes.xyxyn.detach().cpu().numpy()
                scores = result.boxes.conf.detach().cpu().numpy()
                tiers = result.boxes.cls.detach().cpu().numpy().astype(int)
                for box, score, tier in zip(xyxy, scores, tiers):
                    clipped = _clip_and_reject(tuple(float(v) for v in box))
                    if clipped is None:
                        continue
                    batch_raw.append(
                        CPNRawDetection(
                            normalized_xyxy=clipped,
                            confidence=float(score),
                            bandwidth_tier=int(tier),
                        )
                    )
            results_all.append(batch_raw)
    return results_all


class CPNDetector:
    def __init__(
        self,
        checkpoint_path: Path,
        *,
        device: int | str,
    ) -> None:
        from ultralytics import YOLO

        self._checkpoint_path = Path(checkpoint_path)
        self._device = device
        self._model = YOLO(str(self._checkpoint_path))

    def detect_batch(
        self,
        spectrograms: Sequence[LSSTFTSpectrogram],
        *,
        batch_size: int = 16,
    ) -> list[list[CPNProposal]]:
        raw_results = _detect_raw_batch(self, spectrograms, batch_size=batch_size)
        proposals: list[list[CPNProposal]] = []
        for spec, raw in zip(spectrograms, raw_results):
            per_spec = [
                image_box_to_cpn_proposal(
                    det.normalized_xyxy,
                    spec.geometry,
                    bandwidth_tier=det.bandwidth_tier,
                    confidence=det.confidence,
                )
                for det in raw
            ]
            proposals.append(per_spec)
        return proposals
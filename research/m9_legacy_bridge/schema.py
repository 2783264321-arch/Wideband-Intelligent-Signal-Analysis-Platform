"""M9.0 bridge data contracts.

These are thin, locally-defined dataclasses describing (a) the frozen
historical detection record as written by the legacy pipeline, (b) the
platform Recording bounds the adapter validates against, and (c) the
platform-compatible DetectionResult the adapter emits.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class PipelineMetadata:
    id: str
    name: str
    version: str


@dataclass(frozen=True)
class LegacyDetection:
    """One frozen historical detection row from the legacy pipeline.

    Field names match the legacy writer exactly
    (``ZoomSpec/scripts/run_frn_on_proposals.py``).
    """

    sample_id: str
    t0_s: float
    t1_s: float
    f0_hz: float
    f1_hz: float
    class_id: int
    score: float

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LegacyDetection":
        try:
            sample_id = raw["sample_id"]
            t0_s = float(raw["t0_s"])
            t1_s = float(raw["t1_s"])
            f0_hz = float(raw["f0_hz"])
            f1_hz = float(raw["f1_hz"])
            class_id = int(raw["class_id"])
            score = float(raw["score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"legacy detection record is missing or malformed fields: {exc}") from exc

        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError("sample_id must be a non-empty string")
        if class_id < 0:
            raise ValueError("class_id must be non-negative")
        for name, value in (
            ("t0_s", t0_s), ("t1_s", t1_s),
            ("f0_hz", f0_hz), ("f1_hz", f1_hz),
            ("score", score),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")

        return cls(
            sample_id=sample_id,
            t0_s=t0_s,
            t1_s=t1_s,
            f0_hz=f0_hz,
            f1_hz=f1_hz,
            class_id=class_id,
            score=score,
        )


@dataclass(frozen=True)
class RecordingContext:
    """Bounds and identity of the platform Recording a package targets."""

    name: str
    duration_s: float
    frequency_low_hz: float
    frequency_high_hz: float
    dataset: str = "SpaceNet advanced/test"


@dataclass(frozen=True)
class PlatformDetection:
    """Platform DetectionResult-compatible record emitted by the adapter.

    Serializes directly to the Analysis Package v1 ``PackageDetection`` shape
    (``backend/app/imported_runs/schema.py``).
    """

    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str
    confidence: float
    scores: dict[str, float] | None = None

    def to_package_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "t_start_s": self.t_start_s,
            "t_end_s": self.t_end_s,
            "f_low_hz": self.f_low_hz,
            "f_high_hz": self.f_high_hz,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
        }
        if self.scores is not None:
            value["scores"] = self.scores
        return value


@dataclass(frozen=True)
class HistoricalEvaluation:
    """Full-corpus historical metrics; distinct from any single-sample metric."""

    scope: str
    mAP50: float | None
    mAP50_95: float | None
    source_report: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "mAP50": self.mAP50,
            "mAP50_95": self.mAP50_95,
            "source_report": self.source_report,
        }


@dataclass(frozen=True)
class Provenance:
    """Frozen asset hashes recorded at bridge build time."""

    legacy_prediction_sha256: str
    detector_checkpoint_sha256: str
    frn_checkpoint_sha256: str
    config_sha256: str
    extra: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "legacy_prediction_sha256": self.legacy_prediction_sha256,
            "detector_checkpoint_sha256": self.detector_checkpoint_sha256,
            "frn_checkpoint_sha256": self.frn_checkpoint_sha256,
            "config_sha256": self.config_sha256,
        }
        value.update(self.extra)
        return value
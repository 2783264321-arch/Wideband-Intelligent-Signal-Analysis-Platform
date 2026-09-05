"""M9.0 legacy detection adapter.

Turns one frozen historical detection row into a platform-compatible
``PlatformDetection`` after strict validation of identity, physical
coordinates, canonical class mapping, and confidence.

The adapter intentionally has no knowledge of any historical model/DSP
implementation. It only validates and converts coordinates and metadata.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from research.m9_legacy_bridge.schema import (
    LegacyDetection,
    PlatformDetection,
    RecordingContext,
)


class BridgeError(Exception):
    """Base error for the M9 bridge."""


class LegacyRecordError(BridgeError):
    """A legacy record failed structural validation."""


class LegacySampleIdentityMismatch(LegacyRecordError):
    """The legacy sample_id does not match the target Recording."""


class LegacyCoordinateError(LegacyRecordError):
    """The legacy bbox is invalid or falls outside the Recording."""


class LegacyConfidenceMissing(LegacyRecordError):
    """The legacy record has no usable final confidence."""


class LegacyClassError(LegacyRecordError):
    """The legacy class_id is not in the canonical label space."""


def load_label_space(path: str | Path) -> dict[int, str]:
    """Load a platform label-space JSON file as ``{class_id: class_name}``.

    This reuses the platform's canonical ``spacenet_14`` definition rather
    than duplicating a maintained class table in the bridge.
    """
    label_path = Path(path)
    try:
        raw = json.loads(label_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"unable to read label space {label_path}: {exc}") from exc

    classes = raw.get("classes")
    if not isinstance(classes, list):
        raise BridgeError(f"label space {label_path} has no classes list")

    mapping: dict[int, str] = {}
    for item in classes:
        if not isinstance(item, dict):
            raise BridgeError(f"label space {label_path} contains a malformed class entry")
        try:
            class_id = int(item["id"])
            class_name = str(item["name"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BridgeError(f"label space {label_path} class entry is malformed: {exc}") from exc
        mapping[class_id] = class_name
    return mapping


class LegacyDetectionAdapter:
    """Validates and converts legacy detections against a target Recording."""

    def __init__(
        self,
        *,
        recording: RecordingContext,
        label_space: Mapping[int, str],
    ):
        if recording.duration_s <= 0:
            raise BridgeError("recording duration must be positive")
        if recording.frequency_low_hz >= recording.frequency_high_hz:
            raise BridgeError("recording frequency bounds are invalid")
        self.recording = recording
        self.label_space = dict(label_space)
        if not self.label_space:
            raise BridgeError("label space is empty")

    def adapt(self, record: Mapping[str, Any]) -> PlatformDetection:
        if "score" not in record:
            raise LegacyConfidenceMissing(
                "legacy record has no final confidence field; refusing to fabricate"
            )
        try:
            legacy = (
                record
                if isinstance(record, LegacyDetection)
                else LegacyDetection.from_dict(record)
            )
        except ValueError as exc:
            raise LegacyRecordError(str(exc)) from exc

        self._validate_identity(legacy)
        self._validate_bbox(legacy)
        self._validate_class(legacy)
        confidence = self._validate_confidence(legacy)

        return PlatformDetection(
            t_start_s=legacy.t0_s,
            t_end_s=legacy.t1_s,
            f_low_hz=legacy.f0_hz,
            f_high_hz=legacy.f1_hz,
            class_id=legacy.class_id,
            class_name=self.label_space[legacy.class_id],
            confidence=confidence,
            scores=None,
        )

    def _validate_identity(self, legacy: LegacyDetection) -> None:
        if legacy.sample_id != self.recording.name:
            raise LegacySampleIdentityMismatch(
                f"legacy sample_id '{legacy.sample_id}' does not match recording '{self.recording.name}'"
            )

    def _validate_bbox(self, legacy: LegacyDetection) -> None:
        if not (0.0 <= legacy.t0_s < legacy.t1_s <= self.recording.duration_s):
            raise LegacyCoordinateError(
                f"time bbox [{legacy.t0_s}, {legacy.t1_s}] outside recording duration "
                f"[0, {self.recording.duration_s}]"
            )
        if not (
            self.recording.frequency_low_hz
            <= legacy.f0_hz
            < legacy.f1_hz
            <= self.recording.frequency_high_hz
        ):
            raise LegacyCoordinateError(
                f"frequency bbox [{legacy.f0_hz}, {legacy.f1_hz}] outside recording "
                f"[{self.recording.frequency_low_hz}, {self.recording.frequency_high_hz}]"
            )

    def _validate_class(self, legacy: LegacyDetection) -> None:
        if legacy.class_id not in self.label_space:
            raise LegacyClassError(
                f"class_id {legacy.class_id} is not in the canonical label space"
            )

    def _validate_confidence(self, legacy: LegacyDetection) -> float:
        score = legacy.score
        if score is None or not math.isfinite(score):
            raise LegacyConfidenceMissing(
                "legacy record has no usable final confidence; refusing to fabricate"
            )
        if not (0.0 <= score <= 1.0):
            raise LegacyConfidenceMissing(
                f"legacy confidence {score} is outside [0, 1]"
            )
        return score

    def adapt_many(self, records: list[Mapping[str, Any]]) -> list[PlatformDetection]:
        return [self.adapt(record) for record in records]
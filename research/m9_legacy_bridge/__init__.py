"""M9.0 Legacy Real Pipeline Bridge.

Adapts real frozen historical detection outputs (Augmented YOLOv26n CPN +
AHLP + Combined FRN V3) into the platform Analysis Package v1 wire contract.

The bridge only performs identity validation, coordinate/unit conversion,
class mapping, and confidence preservation. It does not re-implement any
historical model, DSP, or training logic.
"""

from research.m9_legacy_bridge.adapter import (
    BridgeError,
    LegacyConfidenceMissing,
    LegacyCoordinateError,
    LegacyDetectionAdapter,
    LegacyRecordError,
    LegacySampleIdentityMismatch,
)
from research.m9_legacy_bridge.exporter import export_package
from research.m9_legacy_bridge.schema import (
    LegacyDetection,
    PipelineMetadata,
    PlatformDetection,
    RecordingContext,
)

__all__ = [
    "BridgeError",
    "LegacyConfidenceMissing",
    "LegacyCoordinateError",
    "LegacyDetection",
    "LegacyDetectionAdapter",
    "LegacyRecordError",
    "LegacySampleIdentityMismatch",
    "PipelineMetadata",
    "PlatformDetection",
    "RecordingContext",
    "export_package",
]
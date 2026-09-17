"""Deterministic Analysis Bundle import fingerprint (idempotence).

The fingerprint is computed from the bundle's *portable* content only (portable
dataset identity, pipeline/parameter identity, and per-sample stable identity +
recording fingerprint + detections). No local path, recording id, dataset id, or
filesystem root ever participates, so the same bundle content imported twice
yields the same fingerprint and is deduplicated by the existing imported-run
semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.benchmarks.manifest import canonical_json_bytes
from app.imported_runs.bundle_schema import AnalysisBundleManifest
from app.imported_runs.fingerprint import canonical_detection_payload
from app.imported_runs.schema import PackageDetection

BUNDLE_IMPORT_FINGERPRINT_SCHEMA = "analysis_bundle_import_fingerprint_v1"


@dataclass(frozen=True)
class CanonicalBundleSample:
    key: str
    recording_fingerprint: str
    detections: tuple[PackageDetection, ...]


def _detection_sort_key(detection: PackageDetection) -> bytes:
    return canonical_json_bytes(canonical_detection_payload(detection))


def build_analysis_bundle_import_fingerprint(
    manifest: AnalysisBundleManifest,
    samples: tuple[CanonicalBundleSample, ...],
) -> str:
    by_key = {sample.key: sample for sample in samples}
    if len(by_key) != len(samples):
        raise ValueError("duplicate sample key in bundle fingerprint input")
    canonical_samples = [
        {
            "key": sample.key,
            "recording_fingerprint": sample.recording_fingerprint,
            "detections": [
                canonical_detection_payload(detection)
                for detection in sorted(sample.detections, key=_detection_sort_key)
            ],
        }
        for sample in (by_key[key] for key in sorted(by_key))
    ]
    payload = {
        "schema": BUNDLE_IMPORT_FINGERPRINT_SCHEMA,
        "bundle_schema_version": manifest.schema_version,
        "dataset": manifest.dataset.model_dump(),
        "pipeline": manifest.pipeline.model_dump(),
        "parameters": manifest.parameters,
        "samples": canonical_samples,
    }
    return sha256(canonical_json_bytes(payload)).hexdigest()

"""Analysis Bundle v1 strict wire contract.

An Analysis Bundle is a portable, path-free packaging of the *results* of a
completed Dataset Analysis (Dataset + Pipeline). It deliberately carries no raw
IQ and no machine-local information: only a portable dataset identity, stable
sample identities, per-sample recording fingerprints, pipeline/parameter
identity, analysis provenance, and the detections that were produced.

The bundle reuses the Analysis Package v1 detection/execution primitives
(``PackageObject`` / ``PipelineMetadata`` / ``PackageDetection``) rather than
defining a second import architecture.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from app.imported_runs.schema import Name, PackageObject, PipelineMetadata

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
LabelSpace = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]*$", max_length=128)]

ANALYSIS_BUNDLE_SCHEMA = "analysis_bundle_v1"
ANALYSIS_BUNDLE_SCHEMA_VERSION = 1
ANALYSIS_BUNDLE_MANIFEST_FILENAME = "analysis_bundle_manifest.json"


class BundleDataset(PackageObject):
    """Portable dataset identity; never a local path."""

    name: Name
    split: Name
    label_space: LabelSpace
    portable_fingerprint: Sha256Hex


class BundleAnalysis(PackageObject):
    """Identity of the analysis run that produced the results."""

    experiment_id: Name
    name: Name
    status: Name
    executor: Name
    evaluation_id: str | None = None


class BundleProvenance(PackageObject):
    exporter_version: Name
    platform_repo_commit: str | None = None
    export_timestamp: str | None = None
    evaluation_summary: dict[str, Any] | None = None
    # The platform exported this bundle from one completed single-sample run,
    # so an importing service may accept partial dataset coverage from it.
    single_sample_export: bool = False


class BundleSample(PackageObject):
    """One stable sample identity plus its portable recording fingerprint."""

    key: Name
    sample_name: Name
    recording_fingerprint: Sha256Hex
    detection_count: Annotated[int, Field(ge=0)]
    detections_path: Name


class AnalysisBundleManifest(PackageObject):
    schema_version: Literal[1]
    bundle_id: Name
    dataset: BundleDataset
    pipeline: PipelineMetadata
    parameters: dict[str, Any] = Field(default_factory=dict)
    analysis: BundleAnalysis
    provenance: BundleProvenance
    samples: Annotated[list[BundleSample], Field(min_length=1, max_length=10_000)]


class BundleRunMapping(PackageObject):
    sample_key: Name
    sample_name: Name
    recording_id: Name
    analysis_run_id: Name


class AnalysisBundleImportSummary(PackageObject):
    schema_version: Literal[1]
    bundle_id: Name
    import_fingerprint: Sha256Hex
    archive_sha256: Sha256Hex
    dataset_id: str | None = None
    dataset_name: Name
    dataset_split: Name
    pipeline_id: Name
    pipeline_version: Name
    label_space: LabelSpace
    sample_count: Annotated[int, Field(ge=0)]
    detection_count: Annotated[int, Field(ge=0)]
    already_imported: bool
    created_runs: Annotated[int, Field(ge=0)]
    existing_runs: Annotated[int, Field(ge=0)]
    created_detections: Annotated[int, Field(ge=0)]
    # Durable first-class Dataset Analysis (imported DatasetExperiment) id.
    dataset_analysis_id: str | None = None
    sample_run_mapping: list[BundleRunMapping]

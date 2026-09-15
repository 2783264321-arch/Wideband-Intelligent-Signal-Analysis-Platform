"""V1 research artifact exporter package.

Reuses the existing generic Batch Analysis Package v1 writer
(``research.m9_legacy_bridge.batch_exporter``); this package adds a
research-result adapter and an operator CLI. It never loads a model, imports
torch/ultralytics, uses CUDA, touches a WISA database, or contacts a remote host.
"""
from research.v1_artifact_exporter.exporter import (
    ResearchBatchRequest,
    ResearchBatchResult,
    ResearchSample,
    build_batch_manifest,
    export_research_batch,
)
from research.v1_artifact_exporter.predictions import (
    PredictionError,
    adapt_detections,
    load_predictions_jsonl,
)
from research.v1_artifact_exporter.sources import (
    MissingDatasetSampleError,
    UnexpectedSampleError,
    build_research_samples,
    list_split_sample_ids,
    resolve_split_dir,
)

__all__ = [
    "MissingDatasetSampleError",
    "PredictionError",
    "ResearchBatchRequest",
    "ResearchBatchResult",
    "ResearchSample",
    "UnexpectedSampleError",
    "adapt_detections",
    "build_batch_manifest",
    "build_research_samples",
    "export_research_batch",
    "list_split_sample_ids",
    "load_predictions_jsonl",
    "resolve_split_dir",
]

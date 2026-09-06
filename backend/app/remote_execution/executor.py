"""Injected dependency interface for remote executor availability.

Task 4 defines the protocol only; the production implementation arrives in a
later task and consults the configured remote profile. No network or SSH is
touched here.
"""
from __future__ import annotations

from typing import Protocol

from app.analysis.schema import ExecutorAvailabilityRead
from app.pipelines.base import PipelineDefinition
from app.recordings.model import RecordingModel


class RemoteExecutorProbe(Protocol):
    def availability(
        self,
        recording: RecordingModel,
        pipeline: PipelineDefinition,
        source_data_sha256: str | None,
    ) -> ExecutorAvailabilityRead:
        ...
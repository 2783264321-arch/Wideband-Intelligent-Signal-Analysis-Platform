from pathlib import Path
from typing import Any

from app.pipelines.base import (
    DetectionPayload,
    ExecutionCapability,
    Pipeline,
    PipelineDefinition,
    PipelineOutput,
    RecordingInput,
)
from app.pipelines.plugin import PipelineRuntimeAdapter, PluginDeclaration
from app.pipelines.stft_energy.detector import EnergyRegion, detect_stft_energy
from app.recordings.reader import read_segment_from_path

# One 20 s, 100 MHz recording is 2e9 complex samples; reading it whole expands to
# ~15 GiB and the STFT matrix to tens of GB. The detector therefore processes the
# recording in bounded blocks. CHUNK_SECONDS caps the samples held at once and
# CHUNK_OVERLAP_SECONDS keeps a signal that straddles a boundary from being split.
CHUNK_SECONDS = 1.0
CHUNK_OVERLAP_SECONDS = 0.02

# Time hop used when adapting to wideband captures (see detect_stft_energy). 1 ms is
# fine enough to separate real bursts yet coarse enough that a signal stays one region.
_AUTO_TIME_RESOLUTION_S = 0.001


def _chunk_bounds(num_samples: int, chunk_samples: int, overlap_samples: int) -> list[tuple[int, int]]:
    """Half-open (start, end) sample ranges covering [0, num_samples) with overlap."""
    if num_samples <= 0:
        return []
    if num_samples <= chunk_samples:
        return [(0, num_samples)]
    step = max(chunk_samples - overlap_samples, 1)
    bounds: list[tuple[int, int]] = []
    start = 0
    while start < num_samples:
        end = min(start + chunk_samples, num_samples)
        bounds.append((start, end))
        if end >= num_samples:
            break
        start += step
    return bounds


def _merge_regions(regions: list[EnergyRegion]) -> list[EnergyRegion]:
    """Fold regions duplicated by chunk overlap, keeping the strongest of each pair."""
    merged: list[EnergyRegion] = []
    for region in sorted(regions, key=lambda item: (item.t_start_s, item.f_low_hz)):
        duplicate_index = None
        for index, kept in enumerate(merged):
            time_overlaps = region.t_start_s <= kept.t_end_s and kept.t_start_s <= region.t_end_s
            freq_overlaps = region.f_low_hz <= kept.f_high_hz and kept.f_low_hz <= region.f_high_hz
            if time_overlaps and freq_overlaps:
                duplicate_index = index
                break
        if duplicate_index is None:
            merged.append(region)
            continue
        kept = merged[duplicate_index]
        if region.confidence > kept.confidence:
            merged[duplicate_index] = region
    return merged


def _definition() -> PipelineDefinition:
    return PipelineDefinition(
        id="stft_energy_detector",
        name="STFT Energy Detector",
        version="1.0",
        label_space="signal_presence_v1",
        recommended_device="CPU",
        cpu_supported=True,
        stages=("stft", "noise_floor", "threshold", "morphology", "connected_components", "confidence"),
        inspectable_stages=(),
        task_capability="detection_localization",
        technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
        parameter_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "nperseg": {"type": "integer", "default": 512},
                "noverlap": {"type": "integer", "default": 256},
                "nfft": {"type": "integer", "default": 512},
                "noise_floor_percentile": {"type": "number", "default": 50.0},
                "threshold_margin_db": {"type": "number", "default": 12.0},
                "closing_size": {"type": "integer", "default": 5},
                "min_area": {"type": "integer", "default": 100},
                "min_duration_s": {"type": "number", "default": 0.0},
                "min_bandwidth_hz": {"type": "number", "default": 0.0},
                "time_resolution_s": {"type": "number", "default": 0.0},
            },
        },
    )


class STFTEnergyDetectorPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return _definition()

    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput:
        workspace.mkdir(parents=True, exist_ok=True)
        num_samples = max(int(round(recording.duration_s * recording.sample_rate_hz)), 0)
        chunk_samples = max(int(round(CHUNK_SECONDS * recording.sample_rate_hz)), 1)
        overlap_samples = max(int(round(CHUNK_OVERLAP_SECONDS * recording.sample_rate_hz)), 0)
        bounds = _chunk_bounds(num_samples, chunk_samples, overlap_samples)

        # Wideband captures need a coarser time grid or one signal shatters into
        # thousands of slivers. Apply it automatically unless the caller tuned it,
        # so short samples keep the exact legacy behaviour.
        effective_parameters = dict(parameters)
        if not effective_parameters.get("time_resolution_s"):
            default_nperseg = _definition().parameter_schema["properties"]["nperseg"]["default"]
            if effective_parameters.get("nperseg", default_nperseg) == default_nperseg:
                effective_parameters["time_resolution_s"] = _AUTO_TIME_RESOLUTION_S

        collected: list[EnergyRegion] = []
        for start, end in bounds:
            iq = read_segment_from_path(
                recording.data_path, recording.data_format, start, end - start
            )
            collected.extend(
                detect_stft_energy(
                    iq,
                    sample_rate_hz=recording.sample_rate_hz,
                    center_frequency_hz=recording.center_frequency_hz,
                    time_offset_s=start / recording.sample_rate_hz,
                    **effective_parameters,
                )
            )

        regions = _merge_regions(collected)
        detections = [
            DetectionPayload(
                t_start_s=region.t_start_s,
                t_end_s=region.t_end_s,
                f_low_hz=region.f_low_hz,
                f_high_hz=region.f_high_hz,
                class_id=0,
                class_name="Signal",
                confidence=region.confidence,
                scores={"detection": region.confidence, "energy_margin_db": region.energy_margin_db},
            )
            for region in regions
        ]
        return PipelineOutput(
            detections=detections,
            artifacts=[],
            run_metadata={
                "kind": "stft_energy_detector",
                "task_capability": "detection_localization",
                "region_count": len(regions),
                "chunk_count": len(bounds),
                "chunk_seconds": CHUNK_SECONDS,
                "chunk_overlap_seconds": CHUNK_OVERLAP_SECONDS,
            },
        )


def create_runtime(*, assets=None, runtime_descriptor=None, output_label_space=None):
    del assets, runtime_descriptor, output_label_space
    return PipelineRuntimeAdapter(STFTEnergyDetectorPipeline())


PLUGIN = PluginDeclaration(
    definition=_definition(),
    runtime_factory_ref="app.pipelines.stft_energy.pipeline:create_runtime",
)

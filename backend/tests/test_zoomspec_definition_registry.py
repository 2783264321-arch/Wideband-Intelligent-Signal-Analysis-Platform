import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import PlatformError
from app.pipelines.base import PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.registry import create_pipeline_registry


def _recording_input() -> RecordingInput:
    return RecordingInput(
        id="rec",
        data_path=Path("/tmp/x.bin"),
        data_format="float16_interleaved_le",
        sample_rate_hz=5_000_000.0,
        center_frequency_hz=2_402_500_000.0,
        frequency_low_hz=2_400_000_000.0,
        frequency_high_hz=2_405_000_000.0,
        duration_s=0.05,
        label_space="spacenet_14",
    )


def test_zoomspec_definition_matches_frozen_contract():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION

    d = ZOOMSPEC_FROZEN_DEFINITION
    assert isinstance(d, PipelineDefinition)
    assert d.id == "zoomspec_yolo26n_aug_combined_frn_v3"
    assert d.name == "ZoomSpec YOLO26n + Combined FRN V3"
    assert d.version == "1.0.0"
    assert d.label_space == "spacenet_14"
    assert d.recommended_device == "GPU"
    assert d.cpu_supported is False
    assert d.task_capability == "detection_classification"
    assert d.executors_supported == ("remote_gpu",)
    assert d.recommended_executor == "remote_gpu"


def test_registry_exposes_zoomspec_without_model_load():
    registry = create_pipeline_registry()
    pipeline = registry.get("zoomspec_yolo26n_aug_combined_frn_v3")
    assert pipeline.definition.id == "zoomspec_yolo26n_aug_combined_frn_v3"


def test_zoomspec_remote_only_stub_cannot_run_locally():
    registry = create_pipeline_registry()
    pipeline = registry.get("zoomspec_yolo26n_aug_combined_frn_v3")
    with pytest.raises(PlatformError) as exc:
        pipeline.run(_recording_input(), {}, Path("/tmp/ws"))
    assert exc.value.code == "EXECUTOR_UNAVAILABLE"


def test_importing_registry_does_not_import_torch_or_ultralytics():
    code = (
        "import sys; "
        "import app.pipelines.registry; "
        "assert 'torch' not in sys.modules, 'torch leaked into control plane'; "
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked into control plane'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_zoomspec_frozen_pipeline_definition_matches_shared_definition():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import ZoomSpecFrozenPipeline

    # Evaluate the definition property WITHOUT invoking the model-loading constructor.
    instance = object.__new__(ZoomSpecFrozenPipeline)
    assert instance.definition is ZOOMSPEC_FROZEN_DEFINITION
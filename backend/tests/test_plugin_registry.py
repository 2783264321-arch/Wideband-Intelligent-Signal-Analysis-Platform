"""TASK A3 — declarative plugin registry and declarations."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.plugin import PipelineRuntimeAdapter, validate_plugin_parameters
from app.pipelines.plugin_registry import (
    PluginHandle,
    PluginRegistry,
    create_plugin_registry,
    discover_plugin_modules,
    load_declaration,
)

_EXPECTED_PLUGIN_IDS = {
    "dummy",
    "stft_energy_detector",
    "zoomspec_yolo26n_aug_combined_frn_v3",
}


def _recording_input(path: Path) -> RecordingInput:
    return RecordingInput(
        id="rec",
        data_path=path,
        data_format="iq",
        sample_rate_hz=1.0,
        center_frequency_hz=0.0,
        frequency_low_hz=0.0,
        frequency_high_hz=1.0,
        duration_s=1.0,
        label_space="spacenet_14",
    )


def test_registry_lists_existing_pipeline_definitions():
    registry = create_plugin_registry()
    ids = {definition.id for definition in registry.list()}
    assert _EXPECTED_PLUGIN_IDS <= ids
    assert all(isinstance(definition, PipelineDefinition) for definition in registry.list())


def test_registry_get_by_id_and_version():
    registry = create_plugin_registry()
    handle = registry.get("dummy", "1.0")
    assert isinstance(handle, PluginHandle)
    assert handle.definition.id == "dummy"
    assert handle.definition.plugin_version == "1.0"


def test_registry_unknown_returns_plugin_not_found():
    registry = create_plugin_registry()
    with pytest.raises(PlatformError) as excinfo:
        registry.get("does_not_exist", "1.0")
    assert excinfo.value.code == "PLUGIN_NOT_FOUND"


def test_plugin_modules_json_and_env_merge(monkeypatch):
    base = discover_plugin_modules()
    assert "app.pipelines.dummy" in base
    assert tuple(dict.fromkeys(base)) == base
    monkeypatch.setenv("WISA_PLUGIN_MODULES", "app.custom.one, app.custom.two")
    merged = discover_plugin_modules()
    assert merged[: len(base)] == base
    assert merged[-2:] == ("app.custom.one", "app.custom.two")


def test_registry_import_is_torch_free():
    code = (
        "import sys; "
        "import app.pipelines.plugin_registry; "
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


def test_zoom_handle_defers_model_load():
    registry = create_plugin_registry()
    handle = registry.get("zoomspec_yolo26n_aug_combined_frn_v3", "1.0.0")
    with pytest.raises(PlatformError) as excinfo:
        handle.load_runtime(assets={}, runtime_descriptor=None, output_label_space=None)
    assert excinfo.value.code == "EXECUTOR_UNAVAILABLE"


def test_handle_load_runtime_requires_keyword_assets_descriptor_label_space():
    registry = create_plugin_registry()
    handle = registry.get("dummy", "1.0")
    with pytest.raises(TypeError):
        handle.load_runtime()


def test_dummy_handle_load_runtime_accepts_and_ignores_assets(tmp_path):
    registry = create_plugin_registry()
    handle = registry.get("dummy", "1.0")
    runtime = handle.load_runtime(
        assets={"unused": tmp_path / "unused.bin"},
        runtime_descriptor=None,
        output_label_space=None,
    )
    assert isinstance(runtime, PipelineRuntimeAdapter)
    output = runtime.execute(_recording_input(tmp_path / "rec.bin"), {}, tmp_path)
    assert isinstance(output, PipelineOutput)


def test_zoomspec_declares_remote_gpu_capability_early():
    declaration = load_declaration("app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition")
    assert declaration.definition.technical_execution_capabilities == (
        ExecutionCapability("remote_gpu", "cuda", "float16"),
        ExecutionCapability("local_cpu", "cpu", "float32"),
    )


def test_zoomspec_input_compatibility_declared_early():
    declaration = load_declaration("app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition")
    definition = declaration.definition
    assert definition.input_compatibility == ("spacenet_14",)
    assert definition.dataset_adapters == ("SpaceNet",)
    assert definition.model_release_required is True
    assert definition.recommended_execution == "remote_gpu"
    assert declaration.runtime_factory_ref is None


def test_registry_declarations_expose_one_declaration_per_definition():
    registry = create_plugin_registry()
    declarations = registry.declarations()
    assert len(declarations) == len(registry.list())
    assert all(hasattr(declaration, "definition") for declaration in declarations)


def test_plugin_registry_accepts_explicit_declarations():
    from app.pipelines.plugin import PluginDeclaration

    declaration = load_declaration("app.pipelines.dummy")
    registry = PluginRegistry([declaration])
    assert registry.get("dummy", "1.0").definition.id == "dummy"
    assert registry.list() == [declaration.definition]


_STFT_SUPPORTED_PARAMETERS = {
    "nperseg": 256,
    "noverlap": 128,
    "nfft": 256,
    "noise_floor_percentile": 40.0,
    "threshold_margin_db": 10.0,
    "closing_size": 3,
    "min_area": 50,
    "min_duration_s": 0.01,
    "min_bandwidth_hz": 1000.0,
}


def test_stft_parameter_schema_accepts_supported_and_rejects_unknown():
    definition = load_declaration("app.pipelines.stft_energy.pipeline").definition
    schema = definition.parameter_schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "required" not in schema
    assert set(schema["properties"]) == set(_STFT_SUPPORTED_PARAMETERS)
    assert schema["properties"]["nperseg"] == {"type": "integer", "default": 512}
    assert schema["properties"]["threshold_margin_db"] == {"type": "number", "default": 12.0}

    validate_plugin_parameters(definition, {})
    validate_plugin_parameters(definition, dict(_STFT_SUPPORTED_PARAMETERS))

    with pytest.raises(PlatformError) as excinfo:
        validate_plugin_parameters(
            definition,
            {**_STFT_SUPPORTED_PARAMETERS, "unknown_parameter": 1},
        )
    assert excinfo.value.code == "PLUGIN_PARAMETERS_INVALID"

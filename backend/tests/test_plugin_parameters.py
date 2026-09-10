"""TASK A2 — plugin runtime contract and parameter-schema subset."""

import inspect
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import PlatformError
from app.pipelines.base import (
    PLUGIN_API_VERSION,
    Pipeline,
    PipelineDefinition,
    PipelineOutput,
    RecordingInput,
)
from app.pipelines.plugin import (
    PipelineRuntimeAdapter,
    PluginDeclaration,
    PluginRuntimeFactory,
    require_supported_plugin_api,
    validate_plugin_parameters,
)


def _definition(**overrides: Any) -> PipelineDefinition:
    fields: dict[str, Any] = {
        "id": "p",
        "name": "P",
        "version": "1.0",
        "label_space": "spacenet_14",
        "recommended_device": "CPU",
        "cpu_supported": True,
        "stages": (),
        "inspectable_stages": (),
    }
    fields.update(overrides)
    return PipelineDefinition(**fields)


def test_empty_schema_rejects_non_empty_parameters():
    definition = _definition(parameter_schema={})
    with pytest.raises(PlatformError) as excinfo:
        validate_plugin_parameters(definition, {"threshold": 1})
    assert excinfo.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_empty_schema_allows_empty_parameters():
    validate_plugin_parameters(_definition(parameter_schema={}), {})


def test_typed_schema_accepts_valid_and_rejects_invalid():
    definition = _definition(
        parameter_schema={
            "type": "object",
            "properties": {"threshold": {"type": "number"}},
            "additionalProperties": False,
        }
    )
    validate_plugin_parameters(definition, {"threshold": 0.5})
    with pytest.raises(PlatformError) as excinfo:
        validate_plugin_parameters(definition, {"threshold": "high"})
    assert excinfo.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_required_and_additional_properties():
    definition = _definition(
        parameter_schema={
            "type": "object",
            "properties": {"threshold": {"type": "number"}},
            "required": ["threshold"],
            "additionalProperties": False,
        }
    )
    with pytest.raises(PlatformError) as missing:
        validate_plugin_parameters(definition, {})
    assert missing.value.code == "PLUGIN_PARAMETERS_INVALID"
    with pytest.raises(PlatformError) as extra:
        validate_plugin_parameters(definition, {"threshold": 0.5, "unknown": 1})
    assert extra.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_enum_and_numeric_bounds():
    definition = _definition(
        parameter_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["a", "b"]},
                "level": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "additionalProperties": False,
        }
    )
    validate_plugin_parameters(definition, {"mode": "a", "level": 3})
    with pytest.raises(PlatformError):
        validate_plugin_parameters(definition, {"mode": "c"})
    with pytest.raises(PlatformError):
        validate_plugin_parameters(definition, {"level": 9})


def test_default_is_not_injected_into_parameters():
    schema = {
        "type": "object",
        "properties": {"threshold": {"type": "number", "default": 0.5}},
        "additionalProperties": False,
    }
    parameters: dict[str, Any] = {}
    validate_plugin_parameters(_definition(parameter_schema=schema), parameters)
    assert parameters == {}


# ---------------------------------------------------------------------------
# FIX ROUND 2 — enforce the closed A2 parameter-schema subset
# ---------------------------------------------------------------------------


def test_non_empty_schema_requires_object_type():
    schema = {"type": "array", "properties": {}, "additionalProperties": False}
    with pytest.raises(PlatformError) as excinfo:
        validate_plugin_parameters(_definition(parameter_schema=schema), {})
    assert excinfo.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_non_empty_schema_requires_explicit_additional_properties_false():
    for schema in (
        {"type": "object", "properties": {"threshold": {"type": "number"}}},
        {
            "type": "object",
            "properties": {"threshold": {"type": "number"}},
            "additionalProperties": True,
        },
    ):
        with pytest.raises(PlatformError) as excinfo:
            validate_plugin_parameters(_definition(parameter_schema=schema), {})
        assert excinfo.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_non_empty_schema_requires_properties_mapping():
    schema = {
        "type": "object",
        "properties": [("threshold", {"type": "number"})],
        "additionalProperties": False,
    }
    with pytest.raises(PlatformError) as excinfo:
        validate_plugin_parameters(_definition(parameter_schema=schema), {})
    assert excinfo.value.code == "PLUGIN_PARAMETERS_INVALID"


@pytest.mark.parametrize(
    "spec",
    [{}, {"type": "array"}, {"type": "object"}, {"type": "null"}, [1, 2]],
)
def test_non_empty_schema_requires_declared_scalar_types(spec):
    schema = {
        "type": "object",
        "properties": {"x": spec},
        "additionalProperties": False,
    }
    with pytest.raises(PlatformError) as excinfo:
        validate_plugin_parameters(_definition(parameter_schema=schema), {})
    assert excinfo.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_api_version_mismatch():
    with pytest.raises(PlatformError) as excinfo:
        require_supported_plugin_api(_definition(plugin_api_version=PLUGIN_API_VERSION + 1))
    assert excinfo.value.code == "PLUGIN_API_INCOMPATIBLE"
    require_supported_plugin_api(_definition(plugin_api_version=PLUGIN_API_VERSION))


class _RecordingStubPipeline(Pipeline):
    def __init__(self) -> None:
        self.calls: list[tuple[RecordingInput, dict[str, Any], Path]] = []

    @property
    def definition(self) -> PipelineDefinition:
        return _definition()

    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput:
        self.calls.append((recording, parameters, workspace))
        return PipelineOutput()


def test_pipeline_runtime_adapter_delegates(tmp_path):
    pipeline = _RecordingStubPipeline()
    adapter = PipelineRuntimeAdapter(pipeline)
    recording = RecordingInput(
        id="rec",
        data_path=tmp_path / "rec.bin",
        data_format="iq",
        sample_rate_hz=1.0,
        center_frequency_hz=0.0,
        frequency_low_hz=0.0,
        frequency_high_hz=1.0,
        duration_s=1.0,
        label_space="spacenet_14",
    )
    output = adapter.execute(recording, {"x": 1}, tmp_path)
    assert isinstance(output, PipelineOutput)
    assert pipeline.calls == [(recording, {"x": 1}, tmp_path)]


def test_plugin_runtime_factory_signature_is_keyword_only():
    signature = inspect.signature(PluginRuntimeFactory.__call__)
    for name in ("assets", "runtime_descriptor", "output_label_space"):
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


def test_plugin_declaration_defaults_runtime_factory_ref_to_none():
    declaration = PluginDeclaration(definition=_definition())
    assert declaration.runtime_factory_ref is None

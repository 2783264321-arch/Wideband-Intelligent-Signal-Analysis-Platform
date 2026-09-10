from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Protocol

from app.core.errors import PlatformError
from app.pipelines.base import PLUGIN_API_VERSION, Pipeline, PipelineDefinition, PipelineOutput, RecordingInput

# Resolved, verified logical-asset-name -> absolute local path (executor-owned).
RuntimeAssets = Mapping[str, Path]

_SUPPORTED_PARAMETER_TYPES = frozenset({"string", "number", "integer", "boolean"})


class PluginRuntime(Protocol):
    def execute(
        self,
        recording: RecordingInput,
        parameters: dict[str, Any],
        workspace: Path,
    ) -> PipelineOutput: ...


class PluginRuntimeFactory(Protocol):
    """Lazy factory resolved by PluginHandle.load_runtime (dotted ref)."""

    def __call__(
        self,
        *,
        assets: RuntimeAssets,
        runtime_descriptor: Any,  # RuntimeDescriptor (app.remote_execution.runtime), typed in Phase D
        output_label_space: Any,  # LabelSpace (app.labels.service), typed in Phase D
    ) -> PluginRuntime: ...


class PipelineRuntimeAdapter:
    """Adapts a legacy in-process Pipeline to PluginRuntime (local CPU plugins)."""

    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    def execute(
        self,
        recording: RecordingInput,
        parameters: dict[str, Any],
        workspace: Path,
    ) -> PipelineOutput:
        return self._pipeline.run(recording, parameters, workspace)


@dataclass(frozen=True)
class PluginDeclaration:
    definition: PipelineDefinition
    runtime_factory_ref: str | None = None


def require_supported_plugin_api(
    definition: PipelineDefinition,
    api_version: int = PLUGIN_API_VERSION,
) -> None:
    if definition.plugin_api_version != api_version:
        raise PlatformError(
            "PLUGIN_API_INCOMPATIBLE",
            f"Plugin '{definition.plugin_id}' declares API version "
            f"{definition.plugin_api_version}, platform requires {api_version}.",
        )


def validate_plugin_parameters(
    definition: PipelineDefinition,
    parameters: dict[str, Any],
) -> None:
    schema = dict(definition.parameter_schema or {})
    if not schema:
        if parameters:
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Plugin '{definition.plugin_id}' accepts no parameters.",
            )
        return

    properties = _validate_parameter_schema(schema)

    for name in schema.get("required", []):
        if name not in parameters:
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Missing required parameter '{name}'.",
            )

    for name, value in parameters.items():
        spec = properties.get(name)
        if spec is None:
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Unexpected parameter '{name}'.",
            )
        _validate_parameter_value(name, spec, value)


def _validate_parameter_schema(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a non-empty parameter schema against the closed A2 subset.

    Supported: a top-level ``object`` with ``additionalProperties`` exactly
    ``False`` and declared properties whose spec is an object with an explicit
    scalar ``type`` (string/number/integer/boolean). No nested/array/object
    parameters are supported; unsupported schemas fail closed.
    """
    if schema.get("type") != "object":
        raise PlatformError(
            "PLUGIN_PARAMETERS_INVALID",
            "Parameter schema must declare a top-level type of 'object'.",
        )
    if schema.get("additionalProperties") is not False:
        raise PlatformError(
            "PLUGIN_PARAMETERS_INVALID",
            "Parameter schema must set additionalProperties to false.",
        )
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise PlatformError(
            "PLUGIN_PARAMETERS_INVALID",
            "Parameter schema 'properties' must be an object.",
        )
    for name, spec in properties.items():
        if not isinstance(spec, Mapping):
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Parameter '{name}' schema must be an object.",
            )
        if spec.get("type") not in _SUPPORTED_PARAMETER_TYPES:
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Parameter '{name}' must declare a scalar type.",
            )
    return properties


def _validate_parameter_value(name: str, spec: Mapping[str, Any], value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise PlatformError(
            "PLUGIN_PARAMETERS_INVALID",
            f"Parameter '{name}' must be a finite number.",
        )

    expected_type = spec.get("type")
    if expected_type is not None:
        if expected_type not in _SUPPORTED_PARAMETER_TYPES:
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Parameter '{name}' declares unsupported type '{expected_type}'.",
            )
        if not _matches_type(expected_type, value):
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Parameter '{name}' must be of type '{expected_type}'.",
            )

    enum = spec.get("enum")
    if enum is not None and value not in enum:
        raise PlatformError(
            "PLUGIN_PARAMETERS_INVALID",
            f"Parameter '{name}' must be one of {list(enum)!r}.",
        )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = spec.get("minimum")
        if minimum is not None and value < minimum:
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Parameter '{name}' must be at least {minimum}.",
            )
        maximum = spec.get("maximum")
        if maximum is not None and value > maximum:
            raise PlatformError(
                "PLUGIN_PARAMETERS_INVALID",
                f"Parameter '{name}' must be at most {maximum}.",
            )


def _matches_type(expected_type: str, value: Any) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False

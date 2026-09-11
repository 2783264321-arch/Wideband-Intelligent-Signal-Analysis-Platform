"""Control-plane pipeline catalog derived from declarative plugin declarations.

The declarative :class:`~app.pipelines.plugin_registry.PluginRegistry`
(``plugin_modules.json`` plus the ``WISA_PLUGIN_MODULES`` seam) is the single
source of truth for which plugins exist. :func:`create_pipeline_registry` derives
the control-plane catalog from that source, so the API and ``AnalysisService``
never hardcode concrete plugin implementations.

The control plane never executes plugin science in-process: execution is owned by
the platform executor providers (local inference worker / remote coordinator),
resolved through ``PluginHandle.load_runtime``. Production entries are therefore
definition-only and fail closed on direct ``run``.

Selection is by ``pipeline_id``. The public request has no version selector, so
the catalog requires exactly one registered ``PluginVersion`` per id and fails
closed on ambiguity rather than inventing a version-ordering rule.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.core.errors import PlatformError
from app.pipelines.base import PipelineDefinition, PipelineOutput, RecordingInput


class _RegisteredPipeline:
    """Definition-only control-plane view of a declaratively registered plugin."""

    __slots__ = ("_handle",)

    def __init__(self, handle) -> None:
        self._handle = handle

    @property
    def definition(self) -> PipelineDefinition:
        return self._handle.definition

    def run(
        self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path
    ) -> PipelineOutput:
        raise PlatformError(
            "EXECUTOR_UNAVAILABLE",
            "Plugin execution is not available in the control-plane process.",
        )


class PipelineRegistry:
    """Control-plane catalog of registered pipelines keyed by pipeline id.

    Production is derived from :func:`create_pipeline_registry` (declarative
    plugin declarations). The iterable constructor is the embedding/test seam.
    """

    def __init__(self, registered: Iterable[Any]) -> None:
        pipelines: dict[str, Any] = {}
        for item in registered:
            definition = item.definition
            if definition.id in pipelines:
                raise PlatformError(
                    "PIPELINE_INCOMPATIBLE",
                    f"Plugin '{definition.id}' is registered with multiple versions; "
                    "the control-plane catalog requires exactly one active version.",
                )
            pipelines[definition.id] = item
        self._pipelines = pipelines

    def list(self) -> list[PipelineDefinition]:
        return [self._pipelines[key].definition for key in sorted(self._pipelines)]

    def get(self, pipeline_id: str) -> Any:
        pipeline = self._pipelines.get(pipeline_id)
        if pipeline is None:
            raise PlatformError(
                "PIPELINE_INCOMPATIBLE", f"Pipeline '{pipeline_id}' is not registered."
            )
        return pipeline


def create_pipeline_registry() -> PipelineRegistry:
    from app.pipelines.plugin_registry import create_plugin_registry

    plugin_registry = create_plugin_registry()
    return PipelineRegistry(
        _RegisteredPipeline(
            plugin_registry.get(declaration.definition.id, declaration.definition.version)
        )
        for declaration in plugin_registry.declarations()
    )

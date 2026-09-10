from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.core.errors import PlatformError
from app.pipelines.base import PipelineDefinition
from app.pipelines.plugin import PluginDeclaration, PluginRuntime, RuntimeAssets

_PLUGIN_MODULES_PATH = Path(__file__).with_name("plugin_modules.json")
_PLUGIN_MODULES_ENV = "WISA_PLUGIN_MODULES"


@dataclass(frozen=True)
class PluginHandle:
    declaration: PluginDeclaration

    @property
    def definition(self) -> PipelineDefinition:
        return self.declaration.definition

    def load_runtime(
        self,
        *,
        assets: RuntimeAssets,
        runtime_descriptor: Any,
        output_label_space: Any,
    ) -> PluginRuntime:
        """Lazily resolve the dotted runtime factory and invoke it keyword-only."""
        factory_ref = self.declaration.runtime_factory_ref
        if factory_ref is None:
            raise PlatformError(
                "EXECUTOR_UNAVAILABLE",
                f"Plugin '{self.definition.plugin_id}' has no runtime factory.",
            )
        module_path, separator, attribute = factory_ref.partition(":")
        if not separator or not module_path or not attribute:
            raise PlatformError(
                "EXECUTOR_UNAVAILABLE",
                f"Plugin '{self.definition.plugin_id}' has an invalid runtime factory reference '{factory_ref}'.",
            )
        factory = getattr(importlib.import_module(module_path), attribute)
        return factory(
            assets=assets,
            runtime_descriptor=runtime_descriptor,
            output_label_space=output_label_space,
        )


class PluginRegistry:
    def __init__(self, declarations: Iterable[PluginDeclaration]) -> None:
        self._handles: dict[tuple[str, str], PluginHandle] = {}
        for declaration in declarations:
            definition = declaration.definition
            self._handles[(definition.plugin_id, definition.plugin_version)] = PluginHandle(declaration)

    def list(self) -> list[PipelineDefinition]:
        return [self._handles[key].definition for key in sorted(self._handles)]

    def declarations(self) -> list[PluginDeclaration]:
        return [self._handles[key].declaration for key in sorted(self._handles)]

    def get(self, plugin_id: str, plugin_version: str) -> PluginHandle:
        handle = self._handles.get((plugin_id, plugin_version))
        if handle is None:
            raise PlatformError(
                "PLUGIN_NOT_FOUND",
                f"Plugin '{plugin_id}' version '{plugin_version}' is not registered.",
            )
        return handle


def discover_plugin_modules() -> tuple[str, ...]:
    """plugin_modules.json entries plus WISA_PLUGIN_MODULES env (comma-separated)."""
    modules: list[str] = []
    if _PLUGIN_MODULES_PATH.exists():
        payload = json.loads(_PLUGIN_MODULES_PATH.read_text(encoding="utf-8"))
        modules.extend(payload.get("modules", []))
    for entry in os.environ.get(_PLUGIN_MODULES_ENV, "").split(","):
        entry = entry.strip()
        if entry:
            modules.append(entry)
    return tuple(dict.fromkeys(modules))


def load_declaration(module_path: str) -> PluginDeclaration:
    declaration = getattr(importlib.import_module(module_path), "PLUGIN", None)
    if declaration is None:
        raise PlatformError(
            "PLUGIN_NOT_FOUND",
            f"Declaration module '{module_path}' does not expose a PLUGIN declaration.",
        )
    return declaration


def create_plugin_registry() -> PluginRegistry:
    return PluginRegistry(load_declaration(module_path) for module_path in discover_plugin_modules())

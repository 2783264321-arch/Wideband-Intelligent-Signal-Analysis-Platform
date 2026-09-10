"""Input compatibility: which recording label spaces a plugin can consume.

This is independent of the plugin's *output* label space. The recording's
``label_space`` is the dataset/input identity defined by the dataset; the
plugin's ``resolved_output_label_space`` describes the classes it emits.

Acceptance rule:

- Explicit ``definition.input_compatibility`` always governs.
- Legacy fallback (empty ``input_compatibility``) preserves pre-M9.2 behavior:
  - ``task_capability == "detection_localization"`` plugins were permissive, so
    any recording label space is accepted;
  - other legacy plugins keep their prior ``recording.label_space ==
    definition.label_space`` compatibility.
"""
from __future__ import annotations

from app.pipelines.base import PipelineDefinition


def is_input_compatible(
    definition: PipelineDefinition,
    recording_label_space: str | None,
) -> bool:
    if definition.input_compatibility:
        return recording_label_space in definition.input_compatibility
    if definition.task_capability == "detection_localization":
        return True
    return recording_label_space == definition.label_space

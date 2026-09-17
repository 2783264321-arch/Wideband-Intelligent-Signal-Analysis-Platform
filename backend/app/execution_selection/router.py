"""``GET /api/executor-selection`` explanation endpoint.

Previews the Auto execution decision for a single Recording or a Dataset scope
using the same resolver/registry seams as real execution. Exactly one scope must
be supplied. The response exposes only bounded, platform-owned information.
"""
from fastapi import APIRouter, Query, Request

from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.datasets.analysis_manifest import build_dataset_analysis_manifest
from app.execution_selection.resolver import resolve_auto_execution
from app.execution_selection.schema import ExecutionCandidateRead, ExecutorSelectionRead
from app.recordings.model import RecordingModel

router = APIRouter(tags=["executor-selection"])

_SCOPE_INVALID = "EXECUTION_SELECTION_REQUEST_INVALID"


def _resolve_release(definition, requested, model_release_store):
    if not definition.model_release_required:
        if requested is not None:
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH", "This plugin does not accept a model release identity."
            )
        return None
    if model_release_store is None:
        raise PlatformError(
            "EXECUTION_CAPABILITY_UNAVAILABLE", "Model release store is not configured."
        )
    return model_release_store.resolve(
        definition.plugin_id, definition.plugin_version, requested
    )


def _to_read(selection) -> ExecutorSelectionRead:
    return ExecutorSelectionRead(
        requested_mode=selection.requested_mode,
        resolved_executor=selection.resolved_executor,
        reason_code=selection.reason_code,
        reason=selection.reason,
        workload_class=selection.workload_class,
        candidates=[
            ExecutionCandidateRead(
                executor=candidate.executor,
                technical=candidate.technical,
                configured=candidate.configured,
                certified=candidate.certified,
                available=candidate.available,
                reason_code=candidate.reason_code,
                reason_message=candidate.safe_reason_message,
            )
            for candidate in selection.candidates
        ],
    )


@router.get("/api/executor-selection", response_model=ExecutorSelectionRead)
def executor_selection(
    request: Request,
    pipeline_id: str = Query(...),
    model_release_id: str | None = Query(None),
    recording_id: str | None = Query(None),
    dataset_id: str | None = Query(None),
    dataset_projection_id: str | None = Query(None),
    dataset_name: str | None = Query(None),
    dataset_split: str | None = Query(None),
    dataset_label_space: str | None = Query(None),
):
    dataset_values = (dataset_name, dataset_split, dataset_label_space)
    dataset_supplied = any(value is not None for value in dataset_values)
    has_dataset_scope = all(value is not None for value in dataset_values)
    if dataset_supplied and not has_dataset_scope:
        raise PlatformError(
            _SCOPE_INVALID,
            "Dataset scope requires dataset_name, dataset_split and dataset_label_space.",
        )
    scope_count = sum(
        [
            recording_id is not None,
            dataset_id is not None,
            dataset_projection_id is not None,
            has_dataset_scope,
        ]
    )
    if scope_count != 1:
        raise PlatformError(
            _SCOPE_INVALID,
            "Provide exactly one scope: recording_id, dataset_id, dataset_projection_id, "
            "or the dataset name/split/label_space triple.",
        )

    state = request.app.state
    executor_registry = getattr(state, "executor_registry", None)
    if executor_registry is None:
        raise PlatformError(
            "EXECUTION_CAPABILITY_UNAVAILABLE", "Executor registry is not configured."
        )
    definition = state.pipeline_registry.get(pipeline_id).definition
    resolved_release = _resolve_release(
        definition, model_release_id, getattr(state, "model_release_store", None)
    )

    with state.database.session_factory() as session:
        if recording_id is not None:
            probe_recording = session.get(RecordingModel, recording_id)
            if probe_recording is None:
                raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
            selection = resolve_auto_execution(
                definition=definition,
                model_release=resolved_release,
                probe_recording=probe_recording,
                executor_registry=executor_registry,
            )
        elif dataset_id is not None:
            # First-class Dataset Analysis membership: ALL members, GT not required.
            manifest = build_dataset_analysis_manifest(session, dataset_id)
            probe_recording = session.get(RecordingModel, manifest.entries[0].recording_id)
            if probe_recording is None:
                raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
            selection = resolve_auto_execution(
                definition=definition,
                model_release=resolved_release,
                probe_recording=probe_recording,
                executor_registry=executor_registry,
                dataset_item_count=manifest.expected_recordings,
            )
        elif dataset_projection_id is not None:
            manifest = DatasetBenchmarkService(session).prepare_projection_manifest(
                dataset_projection_id
            )
            if not manifest.entries:
                raise PlatformError(
                    _SCOPE_INVALID, "Dataset scope requires at least one recording."
                )
            probe_recording = session.get(RecordingModel, manifest.entries[0].recording_id)
            if probe_recording is None:
                raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
            selection = resolve_auto_execution(
                definition=definition,
                model_release=resolved_release,
                probe_recording=probe_recording,
                executor_registry=executor_registry,
                dataset_item_count=manifest.expected_recordings,
            )
        else:
            manifest = DatasetBenchmarkService(session).prepare_manifest(
                dataset_name, dataset_split, dataset_label_space
            )
            if not manifest.entries:
                raise PlatformError(
                    _SCOPE_INVALID, "Dataset scope requires at least one recording."
                )
            probe_recording = session.get(RecordingModel, manifest.entries[0].recording_id)
            if probe_recording is None:
                raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
            selection = resolve_auto_execution(
                definition=definition,
                model_release=resolved_release,
                probe_recording=probe_recording,
                executor_registry=executor_registry,
                dataset_item_count=manifest.expected_recordings,
            )
    return _to_read(selection)
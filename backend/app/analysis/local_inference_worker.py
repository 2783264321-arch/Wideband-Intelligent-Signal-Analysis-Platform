"""M9.2-D2B plugin-native local inference worker.

Runs one ``AnalysisRun`` in a separate configured ML interpreter using the SAME
plugin contract as remote execution::

    PluginHandle.load_runtime(assets=..., runtime_descriptor=..., output_label_space=...)
      -> PluginRuntime.execute(recording_input, parameters, workspace)
      -> AnalysisResultWriter (same persistence / terminal semantics as remote)

The module never imports torch/ultralytics at import time; heavy imports stay
lazy inside the plugin runtime factory. It never uses the legacy control-plane
``app.analysis.worker`` pipeline path. Local and remote share plugin contracts;
only transport/lifecycle differs.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sys
import traceback
from typing import Mapping

from app.analysis.model import AnalysisRunModel
from app.core.config import Settings
from app.core.errors import PlatformError
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.labels.service import LabelSpaceService
from app.pipelines.base import RecordingInput
from app.pipelines.compatibility import is_input_compatible
from app.pipelines.plugin_registry import create_plugin_registry
from app.recordings.model import RecordingModel
from app.remote_execution.assets import verify_assets
from app.remote_execution.model_release import ModelReleaseStore, load_model_release_defaults
from app.remote_execution.runtime import RuntimeDescriptor
from app.remote_execution.validation import AnalysisResultWriter
from app.storage.service import StorageService

logger = logging.getLogger(__name__)

_RUNTIME_GENERATION_ENV = "WSP_LOCAL_INFERENCE_RUNTIME_REF"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})


def _plugins_root() -> Path:
    return Path(__file__).resolve().parents[1] / "pipelines"


def _build_model_release_store() -> ModelReleaseStore:
    plugins_root = _plugins_root()
    return ModelReleaseStore(
        plugins_root,
        load_model_release_defaults(plugins_root / "model_release_defaults.json"),
    )


def _recording_input(recording: RecordingModel, data_root: Path) -> RecordingInput:
    if recording.external_path:
        data_path = Path(recording.external_path).resolve()
    else:
        data_path = (data_root / recording.data_path).resolve()
    return RecordingInput(
        id=recording.id,
        data_path=data_path,
        data_format=recording.data_format,
        sample_rate_hz=recording.sample_rate_hz,
        center_frequency_hz=recording.center_frequency_hz,
        frequency_low_hz=recording.frequency_low_hz,
        frequency_high_hz=recording.frequency_high_hz,
        duration_s=recording.duration_s,
        label_space=recording.label_space,
    )


def resolve_local_assets(
    *,
    deployment_config: Mapping[str, Mapping[str, Path]] | None,
    plugin_id: str,
    plugin_version: str,
    asset_manifest_sha256: str,
) -> dict[str, Path]:
    """Namespace ``(plugin_id, plugin_version, asset_manifest_sha256)`` -> logical -> path.

    Paths are operator-owned absolute local paths; they are never taken from the
    run/wire request, the plugin declaration, the ModelRelease record, or the
    manifest. Any missing/invalid entry fails closed.
    """
    if not isinstance(deployment_config, Mapping):
        raise PlatformError(
            "PIPELINE_ASSET_MISMATCH", "Local asset deployment configuration is not configured."
        )
    namespace = f"{plugin_id}/{plugin_version}/{asset_manifest_sha256}"
    namespaced = deployment_config.get(namespace)
    if not isinstance(namespaced, Mapping) or not namespaced:
        raise PlatformError(
            "PIPELINE_ASSET_MISMATCH", f"No trusted local asset mapping for '{namespace}'."
        )
    resolved: dict[str, Path] = {}
    for logical_name, raw_path in namespaced.items():
        if not isinstance(logical_name, str) or not logical_name:
            raise PlatformError(
                "PIPELINE_ASSET_MISMATCH", "Local asset logical names must be non-empty strings."
            )
        try:
            path = Path(str(raw_path))
        except (TypeError, ValueError) as exc:
            raise PlatformError(
                "PIPELINE_ASSET_MISMATCH", f"Local asset path for '{logical_name}' is invalid."
            ) from exc
        if not path.is_absolute():
            raise PlatformError(
                "PIPELINE_ASSET_MISMATCH",
                f"Local asset path for '{logical_name}' must be absolute.",
            )
        resolved[logical_name] = path
    return resolved


def _resolve_descriptor(metadata: Mapping, run: AnalysisRunModel) -> RuntimeDescriptor:
    descriptor = RuntimeDescriptor.from_metadata(metadata.get("runtime_descriptor"))
    if descriptor is None:
        raise PlatformError(
            "RUNTIME_DESCRIPTOR_INVALID", "Local run is missing a frozen runtime descriptor."
        )
    if descriptor.executor != run.executor:
        raise PlatformError(
            "RUNTIME_DESCRIPTOR_INVALID",
            "Runtime descriptor executor does not match the run executor.",
        )
    # Authoritative local execution-time generation check: the launching provider's
    # immutable generation env MUST equal the frozen descriptor's generation label.
    # environment_ref (the private interpreter path) is intentionally NOT compared.
    actual_generation = os.environ.get(_RUNTIME_GENERATION_ENV, "")
    if not actual_generation:
        raise PlatformError(
            "RUNTIME_DESCRIPTOR_INVALID",
            f"{_RUNTIME_GENERATION_ENV} is not set for the local inference worker.",
        )
    if actual_generation != descriptor.environment_label:
        raise PlatformError(
            "RUNTIME_DESCRIPTOR_INVALID",
            "Local runtime generation does not match the frozen runtime descriptor.",
        )
    return descriptor


def _resolve_run_assets(definition, metadata: Mapping, settings: Settings, model_release_store) -> dict[str, Path]:
    frozen_release_id = metadata.get("model_release_id")
    frozen_manifest_sha = metadata.get("asset_manifest_sha256")

    if definition.model_release_required:
        if not frozen_release_id or not frozen_manifest_sha:
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH",
                "Release-bound local run is missing its model release identity.",
            )
        # Authoritative resolution (never a reverse/index lookup), then exact freeze check.
        resolved = model_release_store.resolve(
            definition.plugin_id, definition.plugin_version, frozen_release_id
        )
        if (
            resolved.release.model_release_id != frozen_release_id
            or resolved.manifest.asset_manifest_sha256 != frozen_manifest_sha
        ):
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH",
                "Frozen model release identity does not match the resolved release.",
            )
        assets = resolve_local_assets(
            deployment_config=settings.local_asset_paths,
            plugin_id=definition.plugin_id,
            plugin_version=definition.plugin_version,
            asset_manifest_sha256=frozen_manifest_sha,
        )
        verify_assets(resolved.manifest, dict(assets))
        return assets

    if frozen_release_id is not None or frozen_manifest_sha is not None:
        raise PlatformError(
            "MODEL_RELEASE_MISMATCH",
            "Release-less local run must not carry a model release identity.",
        )
    return {}


def execute_local_run(
    run_id: str,
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    plugin_registry=None,
    label_service: LabelSpaceService | None = None,
    storage: StorageService | None = None,
    model_release_store: ModelReleaseStore | None = None,
) -> None:
    settings = settings or Settings()
    database = database or Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    plugin_registry = plugin_registry or create_plugin_registry()
    label_service = label_service or LabelSpaceService(settings.label_space_root)
    storage = storage or StorageService(settings.data_root)
    model_release_store = model_release_store or _build_model_release_store()

    try:
        with database.session_factory() as session:
            run = session.get(AnalysisRunModel, run_id)
            if run is None:
                raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run was not found.", 404)
            # Terminal runs are immutable: never reset or re-execute them.
            if run.status in _TERMINAL_STATUSES:
                return
            recording = session.get(RecordingModel, run.recording_id)
            if recording is None:
                raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)

            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            run.error_type = None
            run.error_message = None
            session.commit()

            handle = plugin_registry.get(run.pipeline_id, run.pipeline_version)
            definition = handle.definition
            # Same input-compatibility defense-in-depth as the remote executor
            # (spec §10.3): fail closed before adapter/model work.
            if not is_input_compatible(definition, recording.label_space):
                raise PlatformError(
                    "INPUT_INCOMPATIBLE",
                    "Plugin does not accept this recording dataset label space.",
                )
            metadata = dict(run.execution_metadata_json or {})

            descriptor = _resolve_descriptor(metadata, run)
            assets = _resolve_run_assets(definition, metadata, settings, model_release_store)
            output_label_space = label_service.get(definition.resolved_output_label_space)

            runtime = handle.load_runtime(
                assets=assets,
                runtime_descriptor=descriptor,
                output_label_space=output_label_space,
            )
            workspace = storage.artifact_dir(run.id)
            output = runtime.execute(
                _recording_input(recording, settings.data_root),
                dict(run.parameters_json),
                workspace,
            )
            AnalysisResultWriter(
                session=session,
                label_service=label_service,
                pipeline_definition=definition,
                workspace=workspace,
            ).persist(run=run, recording=recording, output=output)
            session.commit()
    except Exception as exc:
        logger.error("Local inference worker failed for %s\n%s", run_id, traceback.format_exc())
        with database.session_factory() as recovery_session:
            run = recovery_session.get(AnalysisRunModel, run_id)
            # Recovery must never rewrite an already-terminal run.
            if run is not None and run.status not in _TERMINAL_STATUSES:
                run.status = "failed"
                if isinstance(exc, PlatformError):
                    run.error_type = exc.code
                    run.error_message = exc.message[:1000]
                else:
                    run.error_type = type(exc).__name__
                    run.error_message = str(exc)[:1000]
                run.finished_at = datetime.now(timezone.utc)
                recovery_session.commit()
        raise


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(
            "usage: python -m app.analysis.local_inference_worker <run_id>",
            file=sys.stderr,
        )
        return 2
    execute_local_run(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

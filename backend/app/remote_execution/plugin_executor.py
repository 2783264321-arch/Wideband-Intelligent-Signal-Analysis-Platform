"""M9.2-D3A generic remote ``PluginItemExecutor`` core.

Verification/orchestration only; the plugin owns science. The executor depends on
**injected generic seams** (trusted-assets resolver, ``RuntimeDescriptor``,
package publisher) so this module carries no concrete plugin knowledge (no
ZoomSpec imports or constants). It deliberately does NOT touch
``runner._cli_work`` (D3B), ``worker_context`` (D4) or ``package_publisher`` (D5).

The remote wire is release-bound in D3A: the exact wire ``model_release_id`` is
resolved authoritatively and its manifest self-hash must equal the frozen batch
``asset_manifest_sha256``. Remote release-less execution stays fail-closed.

GroundTruth never reaches inference; request-controlled filesystem paths are never
accepted. Module import stays torch/ultralytics-free.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.errors import PlatformError
from app.labels.service import LabelSpaceService
from app.pipelines.compatibility import is_input_compatible
from app.pipelines.plugin import validate_plugin_parameters
from app.remote_execution.assets import verify_assets
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.schema import RemoteExecutionBatchV1, RemoteExecutionItemV1


class PluginItemExecutor:
    """Generic remote item executor implementing the runner's ItemExecutor protocol."""

    def __init__(
        self,
        *,
        batch: RemoteExecutionBatchV1,
        worker: Any,
        plugin_registry: Any,
        adapter_registry: Any,
        model_release_store: Any,
        certificate_store: Any,
        runtime_descriptor: Any,
        trusted_assets_resolver: Callable[..., Any],
        package_publisher: Callable[..., Any],
    ) -> None:
        self._batch = batch
        self._worker = worker
        self._plugin_registry = plugin_registry
        self._adapter_registry = adapter_registry
        self._model_release_store = model_release_store
        self._certificate_store = certificate_store  # reserved for D4/D5
        self._runtime_descriptor = runtime_descriptor
        self._trusted_assets_resolver = trusted_assets_resolver
        self._package_publisher = package_publisher

    def _require_item_in_batch(self, item: RemoteExecutionItemV1) -> None:
        """Defense-in-depth (D3B): the dispatched item must be the exact frozen
        batch item. A caller cannot inject an arbitrary item into a batch context."""
        for candidate in self._batch.items:
            if candidate.item_key == item.item_key:
                if candidate != item:
                    raise PlatformError(
                        "REMOTE_REQUEST_INVALID",
                        "Dispatched item does not match the frozen batch item.",
                    )
                return
        raise PlatformError(
            "REMOTE_REQUEST_INVALID",
            "Dispatched item is not a member of the frozen batch.",
        )

    def execute(self, item: RemoteExecutionItemV1, job_root: Path) -> None:
        self._require_item_in_batch(item)
        remote_started_at = datetime.now(timezone.utc)
        batch = self._batch

        # 1. Frozen runtime provenance: the request must target this deployment.
        if batch.required_remote_runtime_commit != self._worker.required_runtime_commit:
            raise PlatformError(
                "REMOTE_IMPLEMENTATION_MISMATCH",
                "Frozen request runtime does not match the worker deployment.",
            )
        # 2. Canonical request identity.
        if compute_request_sha256(batch) != batch.request_sha256:
            raise PlatformError(
                "REMOTE_REQUEST_INVALID",
                "Frozen request canonical hash does not match its request_sha256.",
            )

        # 3. Exact plugin handle; no concrete plugin-id branches.
        handle = self._plugin_registry.get(batch.pipeline.id, batch.pipeline.version)
        definition = handle.definition
        if definition.id != batch.pipeline.id or definition.version != batch.pipeline.version:
            raise PlatformError(
                "REMOTE_IMPLEMENTATION_MISMATCH",
                "Resolved plugin definition does not match the frozen batch pipeline.",
            )

        # 4. Authoritative release resolution + frozen manifest-SHA check.
        wire_release_id = batch.pipeline.model_release_id
        if not wire_release_id:
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH",
                "Remote execution requires an explicit frozen model_release_id.",
            )
        resolved = self._model_release_store.resolve(
            batch.pipeline.id, batch.pipeline.version, wire_release_id
        )
        if (
            resolved.release.model_release_id != wire_release_id
            or resolved.manifest.asset_manifest_sha256 != batch.asset_manifest_sha256
        ):
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH",
                "Resolved model release does not match the frozen request identity.",
            )

        # 5. Input compatibility before any adapter/model work (spec §10.3).
        recording = item.recording
        if not is_input_compatible(definition, recording.label_space):
            raise PlatformError(
                "INPUT_INCOMPATIBLE",
                "Plugin does not accept this recording dataset label space.",
            )

        # 6. Trusted assets via the injected deployment seam. The manifest is the
        # sole asset authority: only declared logical assets may reach the runtime.
        raw_assets = self._trusted_assets_resolver(
            plugin_id=definition.plugin_id,
            plugin_version=definition.plugin_version,
            asset_manifest_sha256=batch.asset_manifest_sha256,
            manifest=resolved.manifest,
        )
        assets = {
            name: raw_assets[name]
            for name in resolved.manifest.assets
            if name in raw_assets
        }
        verify_assets(resolved.manifest, assets)

        # 7. Recording resolution only through the DatasetAdapter (GT stays out).
        adapter = self._adapter_registry.get(recording.dataset_name)
        resolved_input = adapter.resolve(
            split=recording.dataset_split,
            key=recording.dataset_key,
            label_space=recording.label_space,
            expected_fingerprint=recording.expected_recording_fingerprint,
            expected_source_hash=recording.expected_source_data_sha256,
            label_space_root=self._worker.label_space_root,
        )

        # 8. Validate parameters before any runtime construction.
        validate_plugin_parameters(definition, item.parameters)

        # 9. Output label space, then the exact load_runtime contract, then execute.
        label_space = LabelSpaceService(self._worker.label_space_root).get(
            definition.resolved_output_label_space
        )
        runtime = handle.load_runtime(
            assets=assets,
            runtime_descriptor=self._runtime_descriptor,
            output_label_space=label_space,
        )
        workspace = Path(job_root) / "work" / item.item_key
        output = runtime.execute(resolved_input.recording_input, item.parameters, workspace)

        # 10. Hand the generic result to the injected package/publish seam.
        remote_finished_at = datetime.now(timezone.utc)
        if remote_finished_at < remote_started_at:
            raise PlatformError(
                "REMOTE_RESULT_INVALID", "remote_finished_at precedes remote_started_at."
            )
        self._package_publisher(
            output=output,
            pipeline_definition=definition,
            output_label_space=label_space,
            runtime_descriptor=self._runtime_descriptor,
            item=item,
            batch=batch,
            job_root=Path(job_root),
            workspace=workspace,
            asset_manifest_sha256=resolved.manifest.asset_manifest_sha256,
            remote_started_at=remote_started_at,
            remote_finished_at=remote_finished_at,
        )

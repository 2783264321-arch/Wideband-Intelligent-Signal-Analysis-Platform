# M9.2 Inference Plugin & Execution Framework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn WISA from a one-model integration into a multi-model inference platform by implementing M9.2A→F in dependency order, without rewriting the proven M9.1 remote control plane.

**Architecture:** Additive plugin framework around the frozen M9.1 core. Plugins are torch-free declarations resolved through a registry; ModelReleases reference the frozen AssetManifest V1 by hash; DatasetAdapters own dataset layout; an ExecutorRegistry + RuntimeDescriptor replace literal dispatch and `cuda:0`; a generic `PluginItemExecutor` replaces the ZoomSpec literal in `runner._cli_work`; ZoomSpec migrates as the golden plugin; a CPN-only bandwidth-tier plugin proves zero control-plane logic edits.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Pydantic v2, pytest; frontend React/TypeScript/Vite/Vitest. No GPU and no real SSH for A–F.

**Spec:** `docs/superpowers/specs/2026-09-10-m9-2-inference-plugin-execution-framework-design.md`

**Base:** `feature/m9-2-inference-plugin-framework @ 28f12dd30587e68e742c788b27271376737f86d0`

---

## Scope And Boundaries

**In scope (M9.2A–F):**
- Plugin identity / PluginVersion (opaque) + extended `PipelineDefinition` + declarative `PluginRegistry`.
- Parameter schema validation; input-compatibility vs output-label-space split.
- ModelRelease records referencing the **unchanged** AssetManifest V1 hash; release-scoped resolution and default-release selection.
- DatasetAdapter boundary and generic recording resolver.
- ExecutorRegistry + RuntimeDescriptor + platform-owned ExecutionCertificate; descriptor-driven probe and package projection.
- Generic `PluginItemExecutor` + runner registry seam; plugin-native local inference worker sharing the same Plugin/ModelRelease/`PluginRuntime` contract (transport/lifecycle differ only).
- Unified runtime factory contract `PluginHandle.load_runtime(*, assets, runtime_descriptor, output_label_space)`.
- `runtime_ref`-bound execution certificates (remote commit-bound; local immutable generation).
- ZoomSpec golden plugin migration (scientific semantics unchanged) + CPU feasibility status recorded, not certified.
- CPN-only/YOLO bandwidth-tier second plugin proving plugin-only integration.

**Out of scope (must not be implemented):**
- Changes to coordinator / fencing / recovery / SSH transport.
- M9.3 batch evaluation, benchmark protocol changes, multi-model scheduling.
- Training platform, model registry service, multi-GPU/vendor abstraction, streaming, CDN.
- Any second `DetectionResult` persistence path or second package schema.
- Real GPU/SSH execution (that is the separate final live gate in §21.1).

**Hard invariants preserved throughout:**
- AssetManifest V1 canonical payload and golden hash `16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08` unchanged.
- Canonical request hash scheme, runtime-commit check, atomic ingest, terminal immutability, physical-box/label validation unchanged.
- Analysis Package v1 unchanged (`schema_version=1`, `extra="forbid"`).
- Control plane and `runner` module imports remain torch/ultralytics-free.
- Pre-M9.2 remote runs remain recoverable from `execution_metadata_json` with byte-exact `request_sha256` reconstruction.

---

## File / Interface Map (before tasks)

### New files

| File | Owner phase | Purpose |
|---|---|---|
| `backend/app/pipelines/plugin.py` | A | `PLUGIN_API_VERSION`, `PluginRuntime`, `PipelineRuntimeAdapter`, `PluginDeclaration`, parameter validation |
| `backend/app/pipelines/plugin_registry.py` | A | `PluginHandle`, `PluginRegistry`, `create_plugin_registry`, module discovery |
| `backend/app/pipelines/plugin_modules.json` | A/F | platform data: declaration module paths (add CPN here) |
| `backend/app/pipelines/model_release_defaults.json` | B | per `(plugin_id, plugin_version)` default release id |
| `backend/app/pipelines/execution_certificates.json` | D | platform-owned certificates |
| `backend/app/remote_execution/model_release.py` | B | `ModelRelease`, `ModelReleaseStore` |
| `backend/app/datasets/adapter.py` | C | `DatasetAdapter`, `ResolvedRecordingInput`, `DatasetAdapterRegistry` |
| `backend/app/remote_execution/runtime.py` | D | `RuntimeDescriptor`, `ExecutionCertificate`, `ExecutionCertificateStore`, `ExecutorProvider`, `ExecutorRegistry` |
| `backend/app/analysis/local_executor.py` | D | `LocalInferenceWorkerProvider` (separate worker process) |
| `backend/app/analysis/local_inference_worker.py` | D | generic plugin-native local worker entrypoint |
| `backend/app/remote_execution/plugin_executor.py` | D3A/D3B | generic `PluginItemExecutor` |
| `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/plugin.py` | E | ZoomSpec `PLUGIN` + lazy runtime factory |
| `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/model_releases/golden.json` | B | golden ModelRelease record (references existing manifest) |
| `backend/app/pipelines/cpn_bandwidth_tier/__init__.py` | F | CPN-only plugin package |
| `backend/app/pipelines/cpn_bandwidth_tier/plugin.py` | F | CPN-only declaration + runtime factory |
| `backend/app/pipelines/cpn_bandwidth_tier/pipeline.py` | F | detector-only composition |
| `label_spaces/cpn_bandwidth_tier_v1.json` | F | output label space (narrow/mid/wide) |

### Modified files

| File | Owner phase | Change |
|---|---|---|
| `backend/app/pipelines/base.py` | A | add `ExecutionCapability`; extend `PipelineDefinition` additively |
| `backend/app/pipelines/dummy.py`, `stft_energy/pipeline.py` | A | expose `PLUGIN` declaration |
| `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py` | A/E | expose `PLUGIN` declaration |
| `backend/app/analysis/schema.py` | A/C | additive read-model fields (`output_label_space`, `input_compatibility`, `model_release_required`) |
| `backend/app/analysis/router.py` | A | map new fields in `list_pipelines` |
| `frontend/src/api/types.ts` | A | optional additive fields |
| `backend/app/remote_execution/assets.py` | B | manifest-scoped loader helper; V1 payload untouched |
| `backend/app/remote_execution/request_builder.py` | B/C | thread `model_release_id` (wire + metadata); freeze validated parameters |
| `backend/app/remote_execution/schema.py` | B | optional `model_release_id` on `RemotePipelineRefV1` + envelope |
| `backend/app/remote_execution/canonical.py` | B | omit `None` optional fields so legacy request SHA stays byte-exact |
| `backend/app/remote_execution/identity.py` | B | manifest hash by (plugin, release) |
| `backend/app/main.py` | B | manifest/release path wiring (no ZoomSpec literal) |
| `backend/app/core/config.py` | D | local interpreter paths, local runtime generation refs, local asset paths, work root |
| `backend/app/remote_execution/resolver.py` | C | delegate `resolve_space_net` to `DatasetAdapterRegistry` |
| `backend/app/analysis/service.py` | C/D | parameter validation, input compatibility, capability dispatch, resolved release |
| `backend/app/remote_execution/validation.py` | C | label validation via `resolved_output_label_space` |
| `backend/app/remote_execution/probe.py` | D | descriptor-driven readiness |
| `backend/app/remote_execution/worker_context.py` | D | namespaced trusted asset mapping |
| `backend/app/remote_execution/package_publisher.py` | D | generic definition + descriptor projection |
| `backend/app/remote_execution/result_ingestor.py` | D | validate against persisted descriptor projection |
| `backend/app/remote_execution/runner.py` | D | registry seam in `_cli_work` only |
| `backend/app/remote_execution/zoomspec_executor.py` | E | retired; replaced by plugin runtime + generic executor |

### New tests

| Test file | Phase |
|---|---|
| `backend/tests/test_plugin_definition.py` | A |
| `backend/tests/test_plugin_parameters.py` | A |
| `backend/tests/test_plugin_registry.py` | A |
| `backend/tests/test_pipeline_read_model.py` | A |
| `backend/tests/test_model_release.py` | B |
| `backend/tests/test_asset_manifest_v1_frozen.py` | B |
| `backend/tests/test_request_release_provenance.py` | B |
| `backend/tests/test_release_wiring.py` | B |
| `backend/tests/test_zoomspec_golden_release.py` | B |
| `backend/tests/test_dataset_adapter.py` | C |
| `backend/tests/test_plugin_parameters_freeze.py` | C |
| `backend/tests/test_input_output_label_space.py` | C |
| `backend/tests/test_runtime_descriptor.py` | D |
| `backend/tests/test_execution_certificate.py` | D |
| `backend/tests/test_executor_registry.py` | D |
| `backend/tests/test_plugin_executor.py` | D |
| `backend/tests/test_local_worker_provider.py` | D |
| `backend/tests/test_local_inference_worker.py` | D |
| `backend/tests/test_zoomspec_plugin.py` | E |
| `backend/tests/test_cpn_bandwidth_tier_plugin.py` | F |
| `backend/tests/test_no_plugin_id_in_control_plane.py` | F |
| `backend/tests/test_plugin_genericity.py` | F |

### Test command conventions

- Backend single test: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/<file> -v`
- Backend full: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q`
- Frontend unit: `cd frontend && npm run test -- --run`
- Frontend build: `cd frontend && npm run build`

---

# PHASE 0 — Baseline Guardrails

## TASK 0 — Record baselines (no code commit)

**Goal:** Freeze the reference values every later task must not move, without
depending on a plan-revision SHA that will always be stale by execution time.

Steps:
1. Record the execution start SHA dynamically:
   `IMPLEMENTATION_START_SHA="$(git rev-parse HEAD)"`.
2. Assert no production code changed between the approved architecture baseline
   `28f12dd30587e68e742c788b27271376737f86d0` and `IMPLEMENTATION_START_SHA`:
   `git diff --name-only 28f12dd30587e68e742c788b27271376737f86d0.."$IMPLEMENTATION_START_SHA"`
   MUST contain only `docs/**` paths (the M9.2 spec and plan). Any production
   path (backend/frontend/tests/label_spaces/scripts) appearing here is a
   stop-and-escalate condition.
3. Record golden manifest hash:
   `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -c "from pathlib import Path; from app.remote_execution.assets import load_pipeline_asset_manifest; m=load_pipeline_asset_manifest(Path('backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json')); print(m.asset_manifest_sha256)"`
   Expected: `16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08`.
4. Record the **legacy request hash fixture** used by B3. From a fresh interpreter compute:
   `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -c "from app.remote_execution.request_builder import freeze_request_provenance; m=freeze_request_provenance(local_run_id='run_fixture', recording_fingerprint='a'*64, source_data_sha256='b'*64, dataset_name='SpaceNet', dataset_split='test', dataset_key='0', label_space='spacenet_14', pipeline_id='zoomspec_yolo26n_aug_combined_frn_v3', pipeline_version='1.0.0', required_remote_runtime_commit='5bb5be4b04d04a071bc9d8f4f61172595ecee037', orchestrator_commit='d'*40, asset_manifest_sha256='16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08', remote_profile='remote', id_factory=lambda: 'id_fixture'); print(m['request_sha256'])"`
   Record the printed 64-hex value as `LEGACY_REQUEST_SHA256`. B3 MUST assert this value is unchanged after the wire change.
5. Run full backend suite and frontend suite; record pass counts.
6. Confirm `python -c "import sys; import app.pipelines.registry; assert 'torch' not in sys.modules"` from `PYTHONPATH=backend/.venv` passes.

**No commit.**

---

# PHASE A — Plugin Identity, Parameters, Registry

## TASK A1 — Extend `PipelineDefinition` + add `ExecutionCapability`

**First: read before editing**
- `backend/app/pipelines/base.py` (`PipelineDefinition`, `Pipeline`, `RecordingInput`, `PipelineOutput`).
- `backend/app/pipelines/{dummy.py,stft_energy/pipeline.py,zoomspec_yolo26n_aug_combined_frn_v3/definition.py}` (existing definition construction).

**Interfaces (modify `backend/app/pipelines/base.py`):**

```python
PLUGIN_API_VERSION = 1  # module-level constant in base.py

@dataclass(frozen=True)
class ExecutionCapability:
    executor: str        # "local_cpu" | "local_gpu" | "remote_gpu"
    device_type: str     # "cpu" | "cuda"
    precision: str       # "float32" | "float16"
    def key(self) -> tuple[str, str, str]:
        return (self.executor, self.device_type, self.precision)

@dataclass(frozen=True)
class PipelineDefinition:
    id: str
    name: str
    version: str
    label_space: str
    recommended_device: str
    cpu_supported: bool
    stages: tuple[str, ...]
    inspectable_stages: tuple[str, ...]
    task_capability: str = "classification"
    executors_supported: tuple[str, ...] = ("local_cpu",)
    recommended_executor: str = "local_cpu"
    # --- additive M9.2 fields (all defaulted; existing callers unaffected) ---
    plugin_api_version: int = PLUGIN_API_VERSION
    parameter_schema: Mapping[str, Any] = field(default_factory=dict)
    technical_execution_capabilities: tuple[ExecutionCapability, ...] = ()
    recommended_execution: str | None = None
    model_release_required: bool = False
    input_compatibility: tuple[str, ...] = ()
    output_label_space: str | None = None
    dataset_adapters: tuple[str, ...] = ()

    @property
    def plugin_id(self) -> str: return self.id

    @property
    def plugin_version(self) -> str: return self.version

    @property
    def resolved_output_label_space(self) -> str:
        return self.output_label_space or self.label_space
```

`recommended_device`, `cpu_supported`, `executors_supported`, `recommended_executor` remain as projections. Read-model executor projection semantics (deployment-qualified `executors_supported`; nullable `recommended_executor`; `cpu_supported` is legacy technical metadata only) are fixed by the D2C A+ ruling below.

**RED test (`backend/tests/test_plugin_definition.py`, new):**

```python
def test_execution_capability_key():
    from app.pipelines.base import ExecutionCapability
    assert ExecutionCapability("remote_gpu", "cuda", "float16").key() == ("remote_gpu", "cuda", "float16")

def test_definition_plugin_aliases_and_output_label_space():
    from app.pipelines.base import PipelineDefinition
    d = PipelineDefinition(id="p", name="P", version="1.0", label_space="spacenet_14",
                           recommended_device="CPU", cpu_supported=True, stages=(), inspectable_stages=())
    assert d.plugin_id == "p" and d.plugin_version == "1.0"
    assert d.resolved_output_label_space == "spacenet_14"
    assert d.plugin_api_version == 1

def test_output_label_space_overrides():
    from app.pipelines.base import PipelineDefinition
    d = PipelineDefinition(id="p", name="P", version="1.0", label_space="spacenet_14",
                           recommended_device="CPU", cpu_supported=True, stages=(), inspectable_stages=(),
                           output_label_space="cpn_bandwidth_tier_v1")
    assert d.resolved_output_label_space == "cpn_bandwidth_tier_v1"
```

**Expected failure (RED):** `ImportError`/`AttributeError` for `ExecutionCapability` and new fields.

**Minimal implementation:** add the constant, dataclass, and defaulted fields; add `Mapping`/`field` imports.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_plugin_definition.py -v
```

**Regression:** `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_definition_registry.py backend/tests/test_stft_energy_detector.py -q`

**Commit checkpoint:** `feat: extend pipeline definition with plugin capability fields`

---

## TASK A2 — Parameter validation, `PluginRuntime`, and declaration types

**First: read before editing**
- `backend/app/pipelines/base.py` (Task A1 result).
- `backend/app/core/errors.py` (`PlatformError`).

**Interfaces (create `backend/app/pipelines/plugin.py`):**

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, TYPE_CHECKING

from app.core.errors import PlatformError
from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput, RecordingInput, PLUGIN_API_VERSION

# Resolved, verified logical-asset-name -> absolute local path (executor-owned).
RuntimeAssets = Mapping[str, Path]

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
        runtime_descriptor: Any,          # RuntimeDescriptor (remote_execution.runtime)
        output_label_space: Any,          # labels.service.LabelSpace
    ) -> PluginRuntime: ...

class PipelineRuntimeAdapter:
    """Adapts a legacy in-process Pipeline to PluginRuntime (local CPU plugins)."""
    def __init__(self, pipeline: Pipeline) -> None: ...
    def execute(self, recording, parameters, workspace) -> PipelineOutput:
        return self._pipeline.run(recording, parameters, workspace)

@dataclass(frozen=True)
class PluginDeclaration:
    definition: PipelineDefinition
    runtime_factory_ref: str | None = None   # "module:callable", lazy

def validate_plugin_parameters(definition: PipelineDefinition, parameters: dict[str, Any]) -> None:
    """Validate against the JSON-Schema subset; raise PlatformError('PLUGIN_PARAMETERS_INVALID')."""

def require_supported_plugin_api(definition: PipelineDefinition, api_version: int = PLUGIN_API_VERSION) -> None:
    """Raise PlatformError('PLUGIN_API_INCOMPATIBLE') if definition.plugin_api_version != api_version."""
```

**Frozen factory contract (single interface across A/D/E/F):**

```text
PluginHandle.load_runtime(*, assets, runtime_descriptor, output_label_space) -> PluginRuntime
factory(*, assets, runtime_descriptor, output_label_space) -> PluginRuntime
```

`runtime_descriptor` and `output_label_space` are typed by
`app.remote_execution.runtime.RuntimeDescriptor` and
`app.labels.service.LabelSpace`; `plugin.py` imports them only under
`TYPE_CHECKING` to stay torch-free and cycle-free. Legacy `Dummy`/`STFT` factories
accept all three keyword arguments and ignore the ones they do not need.

`validate_plugin_parameters` supported subset: top-level `type == "object"`, `properties`, `required`, `additionalProperties is False`, per-property `type` in `{string,number,integer,boolean}`, `enum`, numeric `minimum`/`maximum`, `default` (no default mutation). Empty schema `{}` means "no parameters allowed except `{}`".

**RED test (`backend/tests/test_plugin_parameters.py`, new):**

```python
def test_empty_schema_rejects_non_empty_parameters(): ...   # PLUGIN_PARAMETERS_INVALID
def test_empty_schema_allows_empty_parameters(): ...
def test_typed_schema_accepts_valid_and_rejects_invalid(): ...
def test_required_and_additional_properties(): ...
def test_enum_and_numeric_bounds(): ...
def test_api_version_mismatch(): ...                        # PLUGIN_API_INCOMPATIBLE
def test_pipeline_runtime_adapter_delegates(): ...
def test_plugin_runtime_factory_signature_is_keyword_only(): ...
    # inspect.signature of a sample factory has keyword-only assets/runtime_descriptor/output_label_space
```

**Expected failure (RED):** `ModuleNotFoundError: app.pipelines.plugin`.

**Minimal implementation:** create `plugin.py` with the schema-subset validator and adapter.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_plugin_parameters.py -v
```

**Commit checkpoint:** `feat: add plugin parameters and runtime declaration types`

---

## TASK A3 — `PluginRegistry` + declarations for existing pipelines

**First: read before editing**
- `backend/app/pipelines/registry.py`.
- `backend/app/pipelines/plugin.py` (Task A2).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py`.

**Interfaces (create `backend/app/pipelines/plugin_registry.py`):**

```python
@dataclass(frozen=True)
class PluginHandle:
    declaration: PluginDeclaration
    @property
    def definition(self) -> PipelineDefinition: ...
    def load_runtime(self, *, assets, runtime_descriptor, output_label_space) -> PluginRuntime:
        """Lazy dotted import of runtime_factory_ref and invoke with all three
        keyword-only arguments. Raise PlatformError('EXECUTOR_UNAVAILABLE') if
        runtime_factory_ref is None."""

class PluginRegistry:
    def __init__(self, declarations: Iterable[PluginDeclaration]) -> None: ...
    def list(self) -> list[PipelineDefinition]: ...
    def declarations(self) -> list[PluginDeclaration]: ...
    def get(self, plugin_id: str, plugin_version: str) -> PluginHandle:
        """Raise PlatformError('PLUGIN_NOT_FOUND') on miss."""

def discover_plugin_modules() -> tuple[str, ...]:
    """plugin_modules.json entries + WISA_PLUGIN_MODULES env (comma-separated)."""

def load_declaration(module_path: str) -> PluginDeclaration:
    """Import module, read its module-level PLUGIN."""

def create_plugin_registry() -> PluginRegistry: ...
```

**Data (create `backend/app/pipelines/plugin_modules.json`):**

```json
{ "modules": [
  "app.pipelines.dummy",
  "app.pipelines.stft_energy.pipeline",
  "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition"
] }
```

**Modify** each listed module to expose a lazy factory with the frozen keyword
signature (unused arguments ignored):

```python
from app.pipelines.plugin import PluginDeclaration, PipelineRuntimeAdapter
PLUGIN = PluginDeclaration(definition=<definition>, runtime_factory_ref="<module>:create_runtime")

def create_runtime(*, assets=None, runtime_descriptor=None, output_label_space=None):
    return PipelineRuntimeAdapter(<PipelineClass>())
```

For `zoomspec.../definition.py`, declare the **technical capability and input
compatibility early** (Phase A) so certificate-driven projections can be turned
on in Phase D without a later identity change, and set
`runtime_factory_ref=None` (the runtime factory is created in Phase E):

```python
from app.pipelines.base import ExecutionCapability
ZOOMSPEC_FROZEN_DEFINITION = PipelineDefinition(
    # ... existing fields unchanged ...
    input_compatibility=("spacenet_14",),
    dataset_adapters=("SpaceNet",),
    model_release_required=True,
    technical_execution_capabilities=(ExecutionCapability("remote_gpu", "cuda", "float16"),),
    recommended_execution="remote_gpu",
)
PLUGIN = PluginDeclaration(definition=ZOOMSPEC_FROZEN_DEFINITION, runtime_factory_ref=None)
```

This preserves the legacy `executors_supported=("remote_gpu",)` /
`recommended_executor="remote_gpu"` projections until Phase D replaces them with
certificate-derived projections.

**RED test (`backend/tests/test_plugin_registry.py`, new):**

```python
def test_registry_lists_existing_pipeline_definitions(): ...
def test_registry_get_by_id_and_version(): ...
def test_registry_unknown_returns_plugin_not_found(): ...
def test_plugin_modules_json_and_env_merge(monkeypatch): ...
def test_registry_import_is_torch_free():   # subprocess: import app.pipelines.plugin_registry
    # assert 'torch' not in sys.modules and 'ultralytics' not in sys.modules
def test_zoom_handle_defers_model_load(): ...   # load_runtime() raises for remote-only
def test_handle_load_runtime_requires_keyword_assets_descriptor_label_space(): ...
    # calling load_runtime() without the three keywords raises TypeError
def test_dummy_handle_load_runtime_accepts_and_ignores_assets(): ...
def test_zoomspec_declares_remote_gpu_capability_early(): ...
    # definition.technical_execution_capabilities == (ExecutionCapability("remote_gpu","cuda","float16"),)
def test_zoomspec_input_compatibility_declared_early(): ...
    # definition.input_compatibility == ("spacenet_14",) and model_release_required is True
```

**Expected failure (RED):** registry module missing; modules lack `PLUGIN`.

**Minimal implementation:** add `plugin_registry.py`, `plugin_modules.json`, `PLUGIN` exports.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_plugin_registry.py -v
```

**Regression / boundary:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_definition_registry.py -q
```

**Commit checkpoint:** `feat: add declarative plugin registry`

---

## TASK A4 — Read model and frontend type additions

**First: read before editing**
- `backend/app/analysis/schema.py` (`PipelineDefinitionRead`).
- `backend/app/analysis/router.py` (`list_pipelines`).
- `frontend/src/api/types.ts` (`PipelineDefinition`).

**Interfaces (modify `backend/app/analysis/schema.py`):**

```python
class PipelineDefinitionRead(BaseModel):
    # ... existing fields unchanged (id/name/version/label_space/... snake_case) ...
    plugin_api_version: int = 1
    output_label_space: str
    input_compatibility: list[str] = []
    dataset_adapters: list[str] = []
    model_release_required: bool = False
    technical_execution_capabilities: list[dict] = []
    recommended_execution: str | None = None
```

`label_space` remains the output-label-space compatibility projection (equal to
`output_label_space`). Router `list_pipelines` continues to build the payload
from `asdict(definition)`; add the derived fields (`output_label_space`,
`input_compatibility`, `dataset_adapters`, `model_release_required`,
`technical_execution_capabilities` as dicts, `recommended_execution`) during
mapping. Keep camelCase out; the existing route uses snake_case fields.

**Modify `frontend/src/api/types.ts`:** add the same optional fields
(`plugin_api_version`, `output_label_space`, `input_compatibility`,
`dataset_adapters`, `model_release_required`, `technical_execution_capabilities`,
`recommended_execution`) to `PipelineDefinition`.

**RED test (`backend/tests/test_pipeline_read_model.py`, new):**

```python
def test_list_pipelines_includes_plugin_fields(client): ...
def test_existing_pipeline_fields_still_present(client): ...
```

**Expected failure (RED):** response lacks new keys.

**Minimal implementation:** additive schema fields; router mapping if needed. Frontend fields are optional so existing views compile.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_pipeline_read_model.py -v
cd frontend && npm run test -- --run src/pages/SpectrumAnalysisPage.test.tsx
```

**Commit checkpoint:** `feat: expose plugin capability read model`

---

# PHASE B — ModelRelease and Frozen AssetManifest V1

## TASK B1 — `ModelRelease` + `ModelReleaseStore` + default resolution

**First: read before editing**
- `backend/app/remote_execution/assets.py` (`PipelineAssetManifest`, `load_pipeline_asset_manifest`, `compute_asset_manifest_sha256`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json`.

**Interfaces (create `backend/app/remote_execution/model_release.py`):**

```python
@dataclass(frozen=True)
class ModelRelease:
    plugin_id: str
    plugin_version: str
    model_release_id: str
    asset_manifest_path: Path          # absolute, resolved inside the plugin package
    asset_manifest_sha256: str

@dataclass(frozen=True)
class ResolvedModelRelease:
    release: ModelRelease
    manifest: PipelineAssetManifest

class ModelReleaseStore:
    def __init__(self, plugins_root: Path, defaults: Mapping[tuple[str, str], str]) -> None:
        """Discover release records at <plugins_root>/<pkg>/model_releases/*.json."""
    def list_releases(self, plugin_id: str, plugin_version: str) -> list[ModelRelease]: ...
    def get(self, plugin_id: str, plugin_version: str, model_release_id: str) -> ModelRelease:
        """Raise PlatformError('MODEL_RELEASE_NOT_FOUND')."""
    def default_release_id(self, plugin_id: str, plugin_version: str) -> str | None: ...
    def resolve(self, plugin_id: str, plugin_version: str, requested: str | None) -> ResolvedModelRelease:
        """Explicit requested or platform default; validate path containment; verify manifest self-hash == record hash."""
    def resolve_by_manifest_sha(self, plugin_id: str, plugin_version: str, asset_manifest_sha256: str) -> ModelRelease:
        """Reverse lookup; raise PlatformError('MODEL_RELEASE_MISMATCH') on zero/multiple matches."""

def load_model_release_defaults(path: Path) -> dict[tuple[str, str], str]: ...
```

**Path-safety rule:** when a record is loaded, `asset_manifest_path` is resolved
against its plugin package root and MUST be contained within that root
(`resolved.is_relative_to(plugin_pkg_root)`); symlink/`..` escape or an absolute
path outside the package raises `PlatformError('MODEL_RELEASE_MISMATCH')`. The
path is never taken from the wire.

**Data (create `backend/app/pipelines/model_release_defaults.json`):** initially `{"defaults": {}}` (the ZoomSpec golden default is added in B5, before any certificate-driven projection).

**Release record format** (stored at `<plugin_pkg>/model_releases/<release_id>.json`):

```json
{ "plugin_id": "...", "plugin_version": "...", "model_release_id": "...",
  "asset_manifest_path": "asset_manifest.json",
  "asset_manifest_sha256": "<64 hex>" }
```

`asset_manifest_path` is repo-relative to the plugin package. The referenced manifest keeps its frozen V1 payload.

**RED test (`backend/tests/test_model_release.py`, new):**

```python
def test_load_defaults_and_resolve_default(): ...
def test_resolve_explicit_release(): ...
def test_unknown_release_raises_not_found(): ...
def test_manifest_hash_mismatch_raises(): ...
def test_resolve_by_manifest_sha_roundtrip(): ...
def test_manifest_path_outside_package_rejected(tmp_path): ...
def test_manifest_path_parent_escape_rejected(tmp_path): ...
def test_two_release_ids_may_share_one_manifest_hash(): ...
```

**Expected failure (RED):** module missing.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_model_release.py -v
```

**Commit checkpoint:** `feat: add model release store and default resolution`

---

## TASK B2 — Freeze AssetManifest V1 (regression + scoped loader)

**First: read before editing**
- `backend/app/remote_execution/assets.py` (`_MANIFEST_FIELDS`, `canonical_asset_manifest_payload`).

**Interfaces (modify `backend/app/remote_execution/assets.py`):**
- Do **not** change `_MANIFEST_FIELDS`, `canonical_asset_manifest_payload`, or `compute_asset_manifest_sha256`.
- Add:

```python
def load_manifest_for_release(manifest_path: Path, expected_sha256: str) -> PipelineAssetManifest:
    """Strict load then assert self-hash == expected_sha256 (PIPELINE_ASSET_MISMATCH)."""
```

**RED test (`backend/tests/test_asset_manifest_v1_frozen.py`, new):**

```python
GOLDEN = "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08"

def test_golden_manifest_hash_unchanged():
    m = load_pipeline_asset_manifest(Path("backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json"))
    assert m.asset_manifest_sha256 == GOLDEN

def test_manifest_payload_has_no_model_release_id():
    raw = json.loads(Path(...).read_text())
    assert "model_release_id" not in raw
    assert set(raw) == {"pipeline_id", "pipeline_version", "assets", "asset_manifest_sha256"}

def test_load_manifest_for_release_accepts_and_rejects(): ...
```

**Expected failure (RED):** `load_manifest_for_release` missing.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_asset_manifest_v1_frozen.py -v
```

**Regression:** `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_assets.py backend/tests/test_remote_request_freeze.py -q`

**Commit checkpoint:** `test: freeze asset manifest v1 golden identity`

---

## TASK B3 — Thread `model_release_id` wire + provenance identity

**Design decision (preserve recovery while identifying release on the wire):**
`model_release_id` **is** part of the internal request wire, but canonical
serialization MUST omit it when `None`, so a legacy request (built before M9.2)
still hashes byte-exactly.

- `RemotePipelineRefV1` gains `model_release_id: WireIdentifier | None = None`.
- `canonical_request_payload` uses `model_dump(exclude={"request_sha256"}, exclude_none=True)`
  so a `None` optional field contributes nothing. (No other batch field is
  optional, so this is a no-op for legacy metadata.)
- `build_batch(metadata)` sets `RemotePipelineRefV1(..., model_release_id=metadata.get("model_release_id"))`.
- New runs hash, transmit, and verify the exact `model_release_id`; two releases
  sharing one `asset_manifest_sha256` remain distinguishable by request identity.
- Local frozen `execution_metadata_json` gains `model_release_id`.
- Remote echoes `model_release_id` in `RemoteExecutionEnvelopeV1`.
- `result_ingestor` verifies envelope vs local metadata **only when local
  metadata carries a non-None key** (legacy-safe).

**First: read before editing**
- `backend/app/remote_execution/schema.py` (`RemotePipelineRefV1`, `RemoteExecutionEnvelopeV1`).
- `backend/app/remote_execution/canonical.py` (`canonical_request_payload`).
- `backend/app/remote_execution/request_builder.py` (`FROZEN_REQUEST_KEYS`, `freeze_request_provenance`, `_build_batch_content`).
- `backend/app/remote_execution/result_ingestor.py` (`_REQUIRED_METADATA_KEYS`, `_verify_envelope_identity`).

**Interfaces:**
- `RemotePipelineRefV1.model_release_id: WireIdentifier | None = None`.
- `canonical_request_payload(batch)` = `batch.model_dump(exclude={"request_sha256"}, exclude_none=True)`.
- `request_builder.freeze_request_provenance(..., model_release_id: str | None = None)` adds `"model_release_id"` to metadata and `FROZEN_REQUEST_KEYS`; `_build_batch_content` passes it into `RemotePipelineRefV1`.
- `RemoteExecutionEnvelopeV1` gains `model_release_id: WireIdentifier | None = None`.
- `result_ingestor._verify_envelope_identity`: if `metadata.get("model_release_id")` is truthy, require equality with `envelope.model_release_id`; else skip (legacy).

**RED test (`backend/tests/test_request_release_provenance.py`, new):**

```python
LEGACY_REQUEST_SHA256 = "<recorded in TASK 0>"

def test_legacy_request_hash_byte_exact():
    batch = build_batch(LEGACY_FIXTURE_METADATA)          # no model_release_id key
    assert batch.request_sha256 == LEGACY_REQUEST_SHA256
    assert compute_request_sha256(batch) == LEGACY_REQUEST_SHA256

def test_none_model_release_id_omitted_from_canonical_payload():
    assert "model_release_id" not in canonical_request_payload(batch)["pipeline"]

def test_two_release_ids_sharing_one_manifest_are_distinguishable():
    a = build_batch({**LEGACY_FIXTURE_METADATA, "model_release_id": "release_a"})
    b = build_batch({**LEGACY_FIXTURE_METADATA, "model_release_id": "release_b"})
    assert a.asset_manifest_sha256 == b.asset_manifest_sha256
    assert a.request_sha256 != b.request_sha256
    assert a.pipeline.model_release_id == "release_a"

def test_request_identification_roundtrip_with_release(): ...
def test_ingest_verifies_release_when_present_and_allows_legacy(): ...
```

**Expected failure (RED):** `RemotePipelineRefV1` rejects `model_release_id`; canonical payload includes `None`; envelope rejects the field.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_request_release_provenance.py -v
```

**Regression:** `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_execution_canonical.py backend/tests/test_remote_request_freeze.py backend/tests/test_remote_result_ingestor.py backend/tests/test_remote_coordinator.py -q`

**Commit checkpoint:** `feat: add model release to request wire identity`

---

## TASK B4 — Control-plane manifest/release wiring (remove literal path)

**First: read before editing**
- `backend/app/main.py` (`_asset_manifest_path`, `_wire_remote_lifecycle`).
- `backend/app/remote_execution/identity.py` (`resolve_asset_manifest_sha256`).
- `backend/app/analysis/service.py` (`asset_manifest_sha256_resolver`, `_create_remote_gpu_run`).

**Interfaces:**
- `main.create_app` builds a `ModelReleaseStore` from `plugin_modules.json` + `model_release_defaults.json` and stores `app.state.model_release_store` **before** the remote-profile try/except, so the store exists even when no remote config is present; remove the ZoomSpec literal path helper.
- `identity.resolve_asset_manifest_sha256` becomes `resolve_asset_manifest_sha256(store, plugin_id, plugin_version, request) -> str` delegating to `ModelReleaseStore.resolve`.
- `AnalysisService` accepts `model_release_store` and an optional requested release; resolves once and uses it for `freeze_request_provenance`.

**RED test (`backend/tests/test_release_wiring.py`, new):**
- `test_app_state_has_model_release_store()` (via `create_app(Settings(...))` with no remote config → store still constructed).
- `test_service_resolves_default_release_into_metadata()` (fake store, fake probe).

**Expected failure (RED):** `app.state.model_release_store` missing; service has no store.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_release_wiring.py -v
```

**Commit checkpoint:** `feat: wire model release resolution into control plane`

---

## TASK B5 — ZoomSpec golden ModelRelease + default (identity metadata early)

**Rationale:** the golden ModelRelease and its default must exist **before**
Phase D activates certificate-driven projections, so D1's seeded certificate is
never dangling and every intermediate commit preserves ZoomSpec remote
availability.

**First: read before editing**
- `backend/app/remote_execution/model_release.py` (Task B1).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json`.

**Interfaces (data only; no logic changes):**
- Create `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/model_releases/golden.json`:

```json
{ "plugin_id": "zoomspec_yolo26n_aug_combined_frn_v3", "plugin_version": "1.0.0",
  "model_release_id": "golden",
  "asset_manifest_path": "asset_manifest.json",
  "asset_manifest_sha256": "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08" }
```

- Modify `backend/app/pipelines/model_release_defaults.json` to add
  `["zoomspec_yolo26n_aug_combined_frn_v3", "1.0.0"] -> "golden"`.

**RED test (`backend/tests/test_zoomspec_golden_release.py`, new):**

```python
def test_golden_release_discovered_and_hash_matches(): ...
def test_default_resolves_to_golden(): ...
def test_golden_manifest_payload_still_v1(): ...
```

**Expected failure (RED):** release record/default absent.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_golden_release.py -v
```

**Commit checkpoint:** `feat: add zoomspec golden model release and default`

---

# PHASE C — DatasetAdapter + Input/Output Split + Parameter Freeze

## TASK C1 — `DatasetAdapter` registry + SpaceNet adapter

**First: read before editing**
- `backend/app/datasets/spacenet.py` (`SpaceNetAdapter`, `SpaceNetSample`).
- `backend/app/remote_execution/resolver.py` (`resolve_space_net`).
- `backend/app/imported_runs/fingerprint.py` (`build_recording_fingerprint`).
- `backend/app/benchmarks/manifest.py` (`ManifestRecording`, `ManifestGroundTruth`).

**Interfaces (create `backend/app/datasets/adapter.py`):**

```python
@dataclass(frozen=True)
class ResolvedRecordingInput:
    recording_fingerprint: str
    source_data_sha256: str
    recording_input: RecordingInput

class DatasetAdapter(Protocol):
    dataset_name: str
    def resolve(self, *, split: str, key: str, label_space: str,
                expected_fingerprint: str, expected_source_hash: str,
                label_space_root: Path) -> ResolvedRecordingInput: ...

class SpaceNetDatasetAdapter:  # wraps SpaceNetAdapter; moves body of resolver.resolve_space_net
    dataset_name = "SpaceNet"

class DatasetAdapterRegistry:
    def get(self, dataset_name: str) -> DatasetAdapter:
        """Raise PlatformError('DATASET_ADAPTER_NOT_FOUND')."""

def create_dataset_adapter_registry() -> DatasetAdapterRegistry: ...
```

- GroundTruth is used only for fingerprint construction and never placed on `RecordingInput` (preserve existing behavior).
- The recording's dataset label space is passed to the adapter for identity; it is not the plugin output label space.

**RED test (`backend/tests/test_dataset_adapter.py`, new):**

```python
def test_spacenet_adapter_resolves_and_verifies(tmp_path): ...
def test_fingerprint_mismatch_raises(): ...
def test_source_hash_mismatch_raises(): ...
def test_unknown_dataset_raises_adapter_not_found(): ...
def test_ground_truth_not_on_recording_input(): ...
```

**Expected failure (RED):** module missing.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_adapter.py -v
```

**Commit checkpoint:** `feat: add dataset adapter registry`

---

## TASK C2 — Generic resolver delegates to adapters

**Interfaces (modify `backend/app/remote_execution/resolver.py`):**

```python
def resolve_recording(adapter_registry, *, dataset_name, split, key, label_space,
                      expected_fingerprint, expected_source_hash, label_space_root) -> ResolvedRecordingInput:
    return adapter_registry.get(dataset_name).resolve(...)

def resolve_space_net(...) -> ResolvedRecordingInput:
    """Deprecated shim; delegates to the SpaceNet adapter (keeps existing callers)."""
```

**RED test:** extend `backend/tests/test_remote_resolver.py` with `test_resolve_recording_dispatches_by_dataset_name` and `test_unknown_dataset_raises`.

**Expected failure (RED):** `resolve_recording` missing.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_resolver.py -v
```

**Commit checkpoint:** `refactor: route recording resolution through dataset adapters`

---

## TASK C3 — Freeze validated parameters (remove hardcoded `{}`)

**First: read before editing**
- `backend/app/remote_execution/request_builder.py:140` (`"parameters": {}`).
- `backend/app/analysis/service.py` (`_create_remote_gpu_run` parameter rejection).
- `backend/app/pipelines/plugin.py` (`validate_plugin_parameters`).

**Interfaces:**
- `freeze_request_provenance(..., parameters: Mapping[str, Any] = {})` stores `dict(parameters)`; `build_batch` already reads `metadata["parameters"]`.
- `AnalysisService.create_run` / `_create_remote_gpu_run`: call `validate_plugin_parameters(definition, parameters)` before freeze; remove the blanket "frozen ZoomSpec rejects any parameters" text, relying on the plugin schema (`{}` schema still rejects non-empty).

**RED test (`backend/tests/test_plugin_parameters_freeze.py`, new):**

```python
def test_empty_schema_run_rejects_parameters(client): ...    # 4xx PLUGIN_PARAMETERS_INVALID
def test_parameters_frozen_into_request_metadata(): ...
def test_request_sha256_changes_with_parameters(): ...
```

**Expected failure (RED):** non-empty params reach metadata or wrong error code.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_plugin_parameters_freeze.py backend/tests/test_remote_create_run.py -v
```

**Regression:** `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_request_freeze.py backend/tests/test_remote_coordinator.py -q`

**Commit checkpoint:** `feat: validate and freeze plugin parameters`

---

## TASK C4 — Input compatibility vs output label space

**First: read before editing**
- `backend/app/analysis/service.py` (`executor_availability`, `create_run` label checks).
- `backend/app/remote_execution/validation.py` (`AnalysisResultWriter.persist` label validation).

**Interfaces:**
- `AnalysisService` checks `recording.label_space in definition.input_compatibility` for all executors (with a legacy fallback: if `input_compatibility` is empty and `definition.task_capability == "detection_localization"`, allow as today).
- `AnalysisResultWriter` validates labels against `self.pipeline_definition.resolved_output_label_space` (fallback `label_space`).
- Error code: `INPUT_INCOMPATIBLE` for input mismatch (preserve `PIPELINE_INCOMPATIBLE` mapping only if existing tests require it; update tests to the new code where this plan changes behavior).

**RED test (`backend/tests/test_input_output_label_space.py`, new):**

```python
def test_input_compatible_output_distinct_allows_run(): ...
def test_recordings_with_wrong_dataset_label_space_rejected(): ...
def test_result_writer_validates_output_label_space_not_input(): ...
def test_detection_localization_legacy_fallback_preserved(): ...
```

**Expected failure (RED):** current service compares `recording.label_space` to `definition.label_space` (output).

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_input_output_label_space.py backend/tests/test_analysis_result_writer.py -v
```

**Commit checkpoint:** `feat: separate input compatibility from output label space`

---

# PHASE D — Executor/Runtime Abstraction

## TASK D1 — `RuntimeDescriptor` + `ExecutionCertificate` store

**First: read before editing**
- `backend/app/remote_execution/probe.py` (`_DEVICE_INDEX = 0`).
- `backend/app/remote_execution/package_publisher.py` (`device=0`, `_LABEL_SPACE`).
- `backend/app/remote_execution/result_ingestor.py:148-155` (`cuda:0` literal).
- `backend/app/pipelines/base.py` (`ExecutionCapability`).

**Interfaces (create `backend/app/remote_execution/runtime.py`):**

```python
@dataclass(frozen=True)
class RuntimeDescriptor:
    executor: str
    device_type: str
    device_index: int | None
    precision: str
    environment_ref: str | None = None    # PRIVATE (internal runtime/profile reference)
    environment_label: str | None = None  # PUBLIC (safe logical label, never a path)
    def public_device(self) -> str | None:
        """'cpu' for cpu; 'cuda:{index}' for cuda; None if unknown."""
    def public_projection(self) -> dict:
        # environment_ref is NEVER returned here; only the safe label or None.
        return {"executor": self.executor, "device": self.public_device(),
                "environment": self.environment_label}
    def to_metadata(self) -> dict:
        """Full internal metadata (includes environment_ref) for execution_metadata_json."""
    @classmethod
    def from_metadata(cls, payload: Mapping[str, Any] | None) -> "RuntimeDescriptor | None": ...

@dataclass(frozen=True)
class ExecutionCertificate:
    plugin_id: str; plugin_version: str; model_release_id: str
    executor: str; device_type: str; precision: str
    runtime_ref: str; evidence_ref: str
    def key(self) -> tuple[str, str, str, str, str, str, str]:
        return (self.plugin_id, self.plugin_version, self.model_release_id,
                self.executor, self.device_type, self.precision, self.runtime_ref)

class ExecutionCertificateStore:
    def __init__(self, certificates: Iterable[ExecutionCertificate]) -> None: ...
    def is_certified(self, *, plugin_id, plugin_version, model_release_id,
                     executor, device_type, precision, runtime_ref) -> bool:
        """True only when ALL fields, including runtime_ref, match exactly."""
    def certified_capabilities(self, *, plugin_id, plugin_version, model_release_id,
                               runtime_ref: str,
                               technical: Iterable[ExecutionCapability]) -> list[ExecutionCapability]:
        """Filter technical capabilities to those with an exact runtime_ref-matching certificate."""

def load_execution_certificates(path: Path) -> list[ExecutionCertificate]: ...
```

**Data (create `backend/app/pipelines/execution_certificates.json`):** seeded with
the M9.1-accepted ZoomSpec **golden** release `remote_gpu`/cuda/float16
certificate and its runtime reference (evidence `m9.1-live-gate`), so Phase D
activates certificate-driven projections against an existing release and the
certificate is never dangling. No `local_cpu` certificate.

```json
{ "certificates": [
  { "plugin_id": "zoomspec_yolo26n_aug_combined_frn_v3", "plugin_version": "1.0.0",
    "model_release_id": "golden", "executor": "remote_gpu", "device_type": "cuda",
    "precision": "float16",
    "runtime_ref": "remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037",
    "evidence_ref": "m9.1-live-gate" } ] }
```

`runtime_ref` for remote MUST be the canonical profile-namespaced form
`remote:{profile.name}:{required_remote_runtime_commit}` (the deployment-profile
identity combined with the pinned code commit; the same commit on two different
profiles MUST NOT share a certificate). Changing either the profile or
`WSP_REMOTE_REQUIRED_RUNTIME_COMMIT` therefore invalidates the certificate above.
Local providers use an operator-owned immutable generation label instead (§D2).

**RED test (`backend/tests/test_runtime_descriptor.py`, `test_execution_certificate.py`):**

```python
def test_public_projection_cpu_and_cuda(): ...
def test_environment_ref_never_in_public_projection():
    d = RuntimeDescriptor("remote_gpu", "cuda", 0, "float16",
                          environment_ref="/root/miniconda3", environment_label="remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037")
    assert d.public_projection()["environment"] == "remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037"
    assert d.public_projection()["environment"] != d.environment_ref
    assert d.environment_ref in d.to_metadata()          # internal only
def test_environment_label_none_projects_none(): ...
def test_descriptor_roundtrip_metadata(): ...
def test_certificate_is_release_and_precision_specific(): ...
def test_different_runtime_ref_is_uncertified():
    # same plugin/release/executor/device/precision, different runtime_ref -> is_certified False
def test_remote_certificate_binds_runtime_commit():
    # cert for remote:<commit_a>; runtime_ref remote:<commit_b> -> is_certified False
def test_uncertified_capability_filtered_out(): ...
def test_loading_seed_certificates(): ...
```

**Expected failure (RED):** modules missing.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_runtime_descriptor.py backend/tests/test_execution_certificate.py -v
```

**Commit checkpoint:** `feat: add runtime descriptor and execution certificates`

---

## TASK D2A — `ExecutorRegistry` + provider primitives (inert)

**Sequencing (controller ruling): D2 is split D2A → D2B → D2B.5 → D2C.**
- **D2A (this task):** add `ExecutorProvider`/`ExecutorRegistry`,
  `RemoteGpuExecutorProvider`, `LocalInferenceWorkerProvider`, and the explicit
  local inference config. No `AnalysisService` dispatch cutover and no read-model
  projection change; accepted execution behavior is unchanged. No production local
  certificate is seeded.
- **D2B:** implement the plugin-native `app.analysis.local_inference_worker` and the
  local resolution sequence (no SSH).
- **D2B.5:** CPU acceptance gate and seed a real local CPU certificate for the
  accepted local runtime generation.
- **D2C:** cut `AnalysisService` + the **deployment-qualified** read-model
  projections over to registry dispatch (only after local certification exists, so
  the Dummy/STFT baseline is preserved). Ruling (A+):
  - `executors_supported` = declared `technical_execution_capabilities` ∩ a
    registered provider ∩ exact `ExecutionCertificate` for the default resolved
    release (`model_release_id=None` for release-less plugins). It is a
    configuration/certification projection, **not** live probe readiness, and must
    not trigger SSH/probe I/O.
  - `recommended_executor` = `definition.recommended_execution` **only when** it is
    in `executors_supported`, else `None`. Backend `PipelineDefinitionRead` field is
    `recommended_executor: str | None`; frontend `recommendedExecutor: string | null`.
    Never substitute another executor.
  - `technical_execution_capabilities` stays the declared truth; `cpu_supported` is
    legacy technical metadata only and never implies `local_cpu` is runnable.
  - Frontend `executorPolicy` derives runnable choices only from
    `executorsSupported` ([] => disabled; local-only => local_cpu; remote-only =>
    remote availability; dual => recommendation/fallback); never infer `local_cpu`
    from `cpuSupported`.
  - `create_run` validates and launches the **exact requested executor**; no
    auto-substitution. Add an exact-executor `ExecutorRegistry` availability seam;
    `/api/executor-availability` may accept an optional `executor` query parameter
    with a backward-compatible default of `remote_gpu`.
  - Generic API tests inject fake certified providers/registry; never restore
    `LocalJobManager`/`app.analysis.worker` and never hardcode `/root/miniconda3`.
    D2B.5 remains the real subprocess acceptance evidence.

**Code-only contract:** if `model_release_required=False`, the resolved
`model_release_id` is `None`. An `ExecutionCertificate` may bind
`model_release_id=None` (release-less/code-only certification). Certification is
**still mandatory** — a provider/runtime without an exact certificate is not
runnable. Do not invent a fake ModelRelease/AssetManifest; plugins that require
immutable external assets must use a real ModelRelease.

**First: read before editing**
- `backend/app/analysis/service.py:80-131,147-220`.
- `backend/app/remote_execution/executor.py` (`RemoteExecutorProbe`, `SshRemoteExecutorProbe`).
- `backend/app/analysis/job_manager.py` (`LocalJobManager` — uses control-plane `sys.executable`; must not be used for plugin inference).
- `backend/app/core/config.py` (`Settings`).

**Interfaces (extend `backend/app/remote_execution/runtime.py`):**

```python
class ExecutorProvider(Protocol):
    name: str
    def runtime_descriptor(self) -> RuntimeDescriptor: ...
    def availability(self, definition, model_release, recording) -> ExecutorAvailabilityRead: ...
    def launch(self, run_id: str, *, coordinator_token: str | None) -> int | None: ...

class ExecutorRegistry:
    def __init__(self, providers: Mapping[str, ExecutorProvider],
                 certificates: ExecutionCertificateStore) -> None: ...
    def provider(self, executor: str) -> ExecutorProvider: ...   # EXECUTION_CAPABILITY_UNAVAILABLE
    def technical_capabilities(self, definition) -> list[ExecutionCapability]: ...
    def certified_capabilities(self, definition, model_release_id, runtime_ref) -> list[ExecutionCapability]: ...
    def availability(self, definition, model_release, recording) -> ExecutorAvailabilityRead: ...
```

- `RemoteGpuExecutorProvider` adapts `SshRemoteExecutorProbe` + `CoordinatorJobManager`; descriptor `executor="remote_gpu", device_type="cuda", precision="float16", environment_ref=<profile name>, environment_label=<profile name>`; `runtime_ref` MUST bind the pinned runtime commit:
  `runtime_ref = f"remote:{profile.name}:{profile.required_remote_runtime_commit}"`.
  Changing `required_remote_runtime_commit` changes `runtime_ref` and therefore invalidates any certificate bound to the old commit.
- `LocalCpuExecutorProvider` / `LocalGpuExecutorProvider` (create `backend/app/analysis/local_executor.py`) do **not** use the control-plane `sys.executable`. They launch a separate plugin-native inference worker (Task D2B) with an explicitly configured interpreter and worker root.
- Local `runtime_ref` is an **operator-owned immutable generation label** (`Settings.local_cpu_runtime_ref` / `local_gpu_runtime_ref`), not derived from the mutable path. Changing the interpreter/dependencies/environment generation requires a new ref and a new certificate.

**Explicit local inference config (modify `backend/app/core/config.py`):**

```python
class Settings(BaseSettings):
    # ... existing ...
    local_cpu_python_path: Path | None = None       # WSP_LOCAL_CPU_PYTHON_PATH
    local_gpu_python_path: Path | None = None       # WSP_LOCAL_GPU_PYTHON_PATH
    local_cpu_runtime_ref: str | None = None        # WSP_LOCAL_CPU_RUNTIME_REF (immutable generation)
    local_gpu_runtime_ref: str | None = None        # WSP_LOCAL_GPU_RUNTIME_REF
    local_inference_work_root: Path | None = None   # WSP_LOCAL_INFERENCE_WORK_ROOT
```

**Local launch interface (`backend/app/analysis/local_executor.py`):**

```python
class LocalInferenceWorkerProvider:
    name = "local_cpu"  # or "local_gpu"
    def __init__(self, *, interpreter: Path, runtime_ref: str, work_root: Path,
                 executor_kind: str, job_manager_factory=None) -> None: ...
    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref
    def runtime_descriptor(self) -> RuntimeDescriptor:
        # executor=self.name, device_type="cpu"|"cuda", precision="float32"|"float16",
        # environment_ref=str(interpreter) (private), environment_label=self._runtime_ref
    def probe(self) -> tuple[bool, str | None]:
        """Run `<interpreter> -c 'import <plugin runtime deps>'` and check work_root;
        return (False, reason) on missing interpreter/deps. Never assume CPU ready."""
    def launch(self, run_id: str, *, coordinator_token: str | None) -> int:
        # Popen([str(self._interpreter), "-m", "app.analysis.local_inference_worker", run_id], ...)
        # shell=False, cwd=backend_root, env includes WSP_PROJECT_ROOT/DATA_ROOT/...
        # NEVER `app.analysis.worker` and NEVER sys.executable
```

- If `local_cpu_python_path` or `local_cpu_runtime_ref` is unset, the provider is not registered and `local_cpu` remains unavailable.
- Control-plane `.venv` stays torch-free; the configured interpreter is a separate ML runtime.
- **(D2C cutover, not D2A)** `AnalysisService` resolves the release, then validates and dispatches the **exact requested executor** through `provider = executor_registry.provider(executor)` (no auto-substitution); remote coord launcher comes from the provider.
- **(D2C cutover, not D2A)** `PipelineDefinition.executors_supported` / `recommended_executor` are the deployment-qualified projection (declared technical capability ∩ registered provider ∩ exact certificate for the default resolved release), not live readiness; `recommended_executor` is `None` unless `recommended_execution` is in `executors_supported`.

**RED test (`backend/tests/test_executor_registry.py`, `test_local_worker_provider.py`):**

```python
def test_dispatch_by_provider_not_literal(): ...
def test_uncertified_executor_unavailable(): ...
def test_local_provider_uses_configured_interpreter_not_control_plane(monkeypatch):
    # intercept Popen argv; assert argv[0] == configured interpreter and != sys.executable
def test_local_provider_launches_plugin_native_entrypoint(monkeypatch):
    # assert argv[1:3] == ["-m", "app.analysis.local_inference_worker"]
def test_local_provider_unset_interpreter_not_registered(): ...
def test_local_provider_unset_runtime_ref_not_registered(): ...
def test_remote_runtime_ref_includes_required_commit(): ...
def test_changing_required_commit_invalidates_certificate(): ...
def test_changing_local_runtime_ref_invalidates_certificate(): ...
def test_certified_capabilities_require_matching_runtime_ref(): ...
def test_projections_reflect_certificates(): ...
```

**Expected failure (RED):** registry/provider missing; service still literal.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_executor_registry.py backend/tests/test_local_worker_provider.py backend/tests/test_remote_executor_availability.py -v
```

**Regression:** `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_create_run.py backend/tests/test_remote_coordinator.py backend/tests/test_remote_transport.py -q`

**Commit checkpoint:** `feat: dispatch execution through executor registry`

---

## TASK D2B — Plugin-native local inference worker (shared contract, no SSH)

**Rationale:** local CPU/GPU must execute through the **same** Plugin →
ModelRelease → assets → RuntimeDescriptor → `PluginHandle.load_runtime` →
`PluginRuntime.execute` → `AnalysisResultWriter` path as remote. Only transport
and lifecycle differ. The legacy `app.analysis.worker` (`create_pipeline_registry`
→ `pipeline.run`) must not be the local execution path for plugin runs.

**First: read before editing**
- `backend/app/analysis/worker.py` (`_recording_input`, `execute_run`, `AnalysisResultWriter` usage).
- `backend/app/remote_execution/model_release.py`, `backend/app/datasets/adapter.py`, `backend/app/pipelines/plugin_registry.py`.
- `backend/app/remote_execution/worker_context.py` (existing trusted-path validation) as the reference for the trusted-local-assets validation style.

D2B defines the **local** resolution sequence directly; the remote equivalent
(`plugin_executor.py`) is implemented later as the D3A generic core and wired into
the runner by D3B, and must match it step for step.
D4 later generalizes the remote namespaced asset mapping; the local mapping uses
the same namespace shape defined here.

**Interfaces (create `backend/app/analysis/local_inference_worker.py`):**

```python
def resolve_local_assets(*, deployment_config: Mapping[str, Mapping[str, Path]],
                         plugin_id: str, plugin_version: str,
                         asset_manifest_sha256: str) -> Mapping[str, Path]:
    """Namespace (plugin_id, plugin_version, asset_manifest_sha256) -> logical -> path; fail closed."""

def execute_local_run(run_id: str, settings: Settings | None = None) -> None:
    # 1 load run + recording from DB
    # 2 metadata = run.execution_metadata_json
    # 3 descriptor = RuntimeDescriptor.from_metadata(metadata["runtime_descriptor"])
    # 4 handle = create_plugin_registry().get(run.pipeline_id, run.pipeline_version)
    # 5 resolved = model_release_store.resolve_by_manifest_sha(
    #        run.pipeline_id, run.pipeline_version, metadata["asset_manifest_sha256"])
    #    verify resolved.model_release_id == metadata["model_release_id"]
    # 6 assets = resolve_local_assets(settings.local_asset_paths, ...)
    # 7 label_space = LabelSpaceService(settings.label_space_root).get(
    #        handle.definition.resolved_output_label_space)   # executor owns resolution
    # 8 runtime = handle.load_runtime(assets=assets, runtime_descriptor=descriptor,
    #                                 output_label_space=label_space)
    # 9 recording_input = _recording_input(recording, settings.data_root)
    # 10 output = runtime.execute(recording_input, dict(run.parameters_json), workspace)
    # 11 AnalysisResultWriter(...).persist(output)  # SAME writer as remote
    # terminal-state handling mirrors worker.execute_run

def main(argv: list[str] | None = None) -> int: ...   # `python -m app.analysis.local_inference_worker <run_id>`
```

- Add `Settings.local_asset_paths: dict | None` (`WSP_LOCAL_ASSET_PATHS_JSON`), namespaced exactly like the remote mapping, validated as absolute safe local paths.
- `local_inference_worker` uses the same `AnalysisResultWriter` and terminal-state semantics as the remote ingest path; it never imports torch at module level (lazy through the plugin runtime).
- Keep legacy `app.analysis.worker` in place for non-plugin/back-compat paths, but the local `ExecutorProvider` launches the new entrypoint.
- Local and remote share the **plugin contracts**; only transport/lifecycle (SSH+coordinator vs direct worker) differ.

**RED test (`backend/tests/test_local_inference_worker.py`, new):**

```python
def test_local_worker_resolves_plugin_release_assets_and_descriptor(): ...
def test_local_worker_calls_handle_load_runtime_with_all_keywords(): ...
def test_local_worker_uses_output_label_space_from_definition(): ...
def test_local_worker_uses_shared_analysis_result_writer(): ...
def test_local_worker_rejects_release_manifest_mismatch(): ...
def test_local_worker_module_import_is_torch_free(): ...
```

**Expected failure (RED):** module missing; local provider still launches `app.analysis.worker`.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_local_inference_worker.py backend/tests/test_local_worker_provider.py -v
```

**Commit checkpoint:** `feat: add plugin-native local inference worker`

---

## TASK D3A — Generic `PluginItemExecutor` core (no runner cutover)

**Sequencing (approved ruling): D3A → D4 → D5 → D3B.** The original single D3 was
BLOCKED with no code change: its trusted-assets and package seams depend on D4/D5,
and cutting `runner._cli_work` before D4 would require ZoomSpec hardcoding. Until
D3B, the existing ZoomSpec remote production path stays intact.

**First: read before editing**
- `backend/app/remote_execution/zoomspec_executor.py` (verification sequence to generalize).
- `backend/app/pipelines/plugin_registry.py`, `backend/app/pipelines/plugin.py`,
  `backend/app/datasets/adapter.py`, `backend/app/remote_execution/model_release.py`,
  `backend/app/remote_execution/runtime.py`.

**Interfaces (create `backend/app/remote_execution/plugin_executor.py`):**

```python
class PluginItemExecutor:
    def __init__(
        self, *, batch, worker, plugin_registry, adapter_registry,
        model_release_store, certificate_store, runtime_descriptor,
        trusted_assets_resolver, package_publisher,
    ) -> None: ...
    def execute(self, item, job_root: Path) -> None:
        # 1 verify batch.required_remote_runtime_commit == worker.required_runtime_commit
        # 2 validate canonical request_sha256
        # 3 handle = plugin_registry.get(batch.pipeline.id, batch.pipeline.version);
        #   verify definition id/version and batch.pipeline.model_release_id
        # 4 release = model_release_store.resolve(exact wire model_release_id);
        #   verify release.manifest.asset_manifest_sha256 == batch.asset_manifest_sha256
        # 5 assets = trusted_assets_resolver(plugin_id, plugin_version, asset_manifest_sha256, manifest)
        # 6 resolved = adapter_registry.get(item.recording.dataset_name).resolve(...)
        # 7 validate_plugin_parameters(handle.definition, item.parameters)
        # 8 label_space = LabelSpaceService(worker.label_space_root).get(
        #       handle.definition.resolved_output_label_space)
        #   runtime = handle.load_runtime(assets=assets, runtime_descriptor=runtime_descriptor,
        #                                 output_label_space=label_space)
        #   output = runtime.execute(resolved.recording_input, item.parameters, workspace)
        # 9 package_publisher(output, handle.definition, runtime_descriptor, item, job_root, ...)
```

- `trusted_assets_resolver` and `package_publisher` are injected generic seams:
  **D4** supplies the production trusted-assets mapping + deployment
  `RuntimeDescriptor`; **D5** supplies the production package publisher. D3A
  defines only the seam protocols and the orchestration.
- D3A MUST NOT modify `runner._cli_work` and MUST NOT reference ZoomSpec-specific
  fields/constants.
- `PluginItemExecutor` MUST call `PluginHandle.load_runtime(assets=...,
  runtime_descriptor=..., output_label_space=...)`; there is no
  `PluginRegistry.load_runtime`. The executor resolves the output `LabelSpace`
  (via `LabelSpaceService`) before invoking the factory.
- Recording resolution happens only through the DatasetAdapter; GroundTruth never
  reaches inference. Request-controlled filesystem paths are never accepted.
- Remote release-less stays fail-closed (the frozen wire requires
  `asset_manifest_sha256`).

**RED test (`backend/tests/test_plugin_executor.py`, new):**

```python
def test_plugin_executor_uses_handle_load_runtime(): ...
def test_plugin_executor_passes_assets_descriptor_and_output_label_space(): ...
def test_plugin_executor_verifies_wire_release_id_and_manifest_hash(): ...
def test_plugin_executor_fails_closed_on_release_mismatch(): ...
def test_plugin_executor_fails_closed_on_asset_mismatch(): ...
def test_plugin_executor_fails_closed_on_parameter_invalid(): ...
def test_plugin_executor_uses_dataset_adapter_only(): ...
def test_plugin_executor_module_import_is_torch_free(): ...
```

**Expected failure (RED):** module missing.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_plugin_executor.py -v
```

**Commit checkpoint:** `feat: add generic remote plugin item executor core`

---

## TASK D4 — Descriptor-driven probe + namespaced worker context

**Supplies D3A's production seams:** the generic `RemoteWorkerContext`
(namespaced trusted asset mapping) and the deployment-owned `RuntimeDescriptor`.
D4 MUST NOT switch `runner._cli_work` (that is D3B).

**First: read before editing**
- `backend/app/remote_execution/probe.py`.
- `backend/app/remote_execution/worker_context.py`.
- `backend/app/remote_execution/profile.py` (`asset_paths`).
- `backend/app/remote_execution/assets.py` (`verify_asset_manifest`).

**Interfaces:**
- `worker_context`: replace the four fixed asset env vars with `WSP_REMOTE_REPO_ROOT`, `WSP_REMOTE_JOB_ROOT`, `WSP_REMOTE_REQUIRED_RUNTIME_COMMIT`, `WSP_REMOTE_MANIFEST_ROOT`, and `WSP_REMOTE_ASSET_PATHS_JSON`. The JSON mapping is namespaced:

```json
{ "<plugin_id>/<plugin_version>/<asset_manifest_sha256>": {
    "<logical_asset_name>": "/abs/safe/posix/path" } }
```

All paths validated with the existing `is_safe_remote_posix_path_text`; unknown/missing entries fail closed with `REMOTE_WORKER_CONTEXT_INVALID`.
- `probe.py`: accept a `RuntimeDescriptor`; assert `device_type == "cpu"` is ready only after interpreter/deps probe, and for `cuda` verify `torch.cuda` device `device_index`. Remove `_DEVICE_INDEX = 0` literal.

**RED test (update `test_remote_worker_context.py`, `test_remote_probe.py`):**

```python
def test_namespaced_asset_mapping_lookup(): ...
def test_unknown_asset_namespace_fails_closed(): ...
def test_unsafe_path_rejected(): ...
def test_probe_uses_descriptor_device_index(): ...
def test_probe_cpu_requires_interpreter_check(): ...
```

**Expected failure (RED):** fixed env names; probe device 0.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_worker_context.py backend/tests/test_remote_probe.py -v
```

**Commit checkpoint:** `feat: make remote probe and worker context descriptor-driven`

---

## TASK D5 — Package publisher + result ingestor descriptor projection

**Supplies D3A's production package seam.** D5 MUST NOT switch `runner._cli_work`
(that is D3B).

**First: read before editing**
- `backend/app/remote_execution/package_publisher.py` (`manifest_for`, `_LABEL_SPACE`, `device=0`).
- `backend/app/remote_execution/result_ingestor.py:134-159`.

**Interfaces:**
- `package_publisher.manifest_for(pipeline_definition, label_space, recording_name, dataset_name, runtime_descriptor)`; `_LABEL_SPACE` removed; no ZoomSpec import. `device` = `runtime_descriptor.public_device()`; `environment` = non-sensitive label or `None`.
- `result_ingestor` expected projection:

```python
descriptor = RuntimeDescriptor.from_metadata((run.execution_metadata_json or {}).get("runtime_descriptor"))
expected = descriptor.public_projection() if descriptor else legacy_projection(run.executor)
# legacy_projection: remote_gpu -> {"executor":"remote_gpu","device":"cuda:0","environment":None}
```

Manifest `execution` must equal `expected`; mismatch → `REMOTE_RESULT_INVALID`.

**RED test (update `test_remote_package_publisher.py`, `test_remote_result_ingestor.py`):**

```python
def test_manifest_uses_descriptor_device_and_label_space(): ...
def test_ingest_accepts_persisted_descriptor_projection(): ...
def test_ingest_rejects_device_not_matching_descriptor(): ...
def test_legacy_run_without_descriptor_still_accepts_cuda0(): ...
```

**Expected failure (RED):** publisher hardcodes ZoomSpec + label space + device 0.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_package_publisher.py backend/tests/test_remote_result_ingestor.py -v
```

**Commit checkpoint:** `feat: project runtime descriptor into package and ingest`

---

## TASK D3B — Final `runner._cli_work` cutover to `PluginItemExecutor`

**Depends on:** D3A (generic executor core) + D4 (namespaced assets + deployment
`RuntimeDescriptor`) + D5 (descriptor-driven package publisher/ingestor). This is
the only task that changes the runner. Until it completes, the existing ZoomSpec
remote production path remains the active path.

**Interfaces (modify `backend/app/remote_execution/runner.py` `_cli_work`):**
- Stop importing `ZoomSpecRemoteItemExecutor`; construct `PluginItemExecutor` from
  the D4/D5 production dependencies: the D4 trusted-assets resolver + deployment
  `RuntimeDescriptor`, the plugin registry, the dataset-adapter registry, the model
  release store, the certificate store, and the D5 package publisher.
- The runner remains the lifecycle/write-once/status owner.
- No concrete plugin-id branches or ZoomSpec constants remain in the runner work
  path; the ZoomSpec remote run now flows through the generic executor unchanged
  scientifically.

**RED test (update `backend/tests/test_remote_runner_work_wiring.py`; add to `backend/tests/test_plugin_executor.py`):**

```python
def test_cli_work_builds_generic_executor(monkeypatch): ...
def test_cli_work_no_zoomspec_executor_import(): ...
```

**Expected failure (RED):** `_cli_work` still imports/constructs `ZoomSpecRemoteItemExecutor`.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_plugin_executor.py backend/tests/test_remote_runner_work_wiring.py -v
```

**Commit checkpoint:** `feat: dispatch remote items through generic plugin executor`

---

# PHASE E — ZoomSpec Golden Migration

## TASK E1 — ZoomSpec runtime factory + plugin module registration

**Rationale:** the ZoomSpec technical capability (A3), golden ModelRelease, and
default (B5) already exist. E1 creates only the lazy runtime factory and points
the plugin registry at it, so identity metadata is not first created inside the
certificate-driven phase.

**First: read before editing**
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/{definition.py,pipeline.py,asset_manifest.json,model_releases/golden.json}`.
- `backend/app/pipelines/plugin.py`, `backend/app/remote_execution/model_release.py`.

**Interfaces (create `.../plugin.py`):**

```python
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
PLUGIN = PluginDeclaration(
    definition=ZOOMSPEC_FROZEN_DEFINITION,   # already carries technical caps + input compat (A3)
    runtime_factory_ref="app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.plugin:build_runtime",
)

def build_runtime(*, assets, runtime_descriptor, output_label_space):
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import ZoomSpecFrozenPipeline
    return _ZoomSpecRuntime(ZoomSpecFrozenPipeline(...), output_label_space=output_label_space)
```

`_ZoomSpecRuntime.execute` maps AssetManifest logical names
(`detector_checkpoint`, `frn_checkpoint`, `ls_stft_normalization`) to constructor
args and calls the frozen pipeline with `{}` parameters. The executor resolves
and passes `output_label_space`; the factory does not resolve label spaces.
Torch stays lazily imported inside the pipeline modules; `plugin.py` itself is
torch-free.

**Data (modify `backend/app/pipelines/plugin_modules.json`):** replace the
`.../definition.py` entry with `app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.plugin`
so the registry loads the declaration that carries the runtime factory (the
`definition.py` module keeps exporting `PLUGIN` for backward compatibility, but
the JSON points at `plugin.py`).

**RED test (`backend/tests/test_zoomspec_plugin.py`, new):**

```python
def test_zoomspec_plugin_declaration_matches_frozen_definition(): ...
def test_zoomspec_runtime_factory_ref_resolves_and_is_torch_free_at_import(): ...
def test_plugin_declaration_import_is_torch_free(): ...   # subprocess: import plugin module
def test_golden_release_still_present_from_b5(): ...
```

**Expected failure (RED):** no `build_runtime` / `plugin.py`; `plugin_modules.json` still points at `definition.py`.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_plugin.py -v
```

**Commit checkpoint:** `feat: add zoomspec plugin runtime factory`

---

## TASK E2 — Route ZoomSpec remote execution through the generic seam

**First: read before editing**
- `backend/app/remote_execution/plugin_executor.py` (Task D3A/D3B).
- `backend/app/remote_execution/zoomspec_executor.py` (retire).
- `backend/tests/test_zoomspec_remote_executor.py`, `backend/tests/test_remote_live_loop.py`.

**Interfaces:**
- `PluginItemExecutor` resolves the ZoomSpec runtime via
  `PluginHandle.load_runtime()` (never `PluginRegistry.load_runtime`) +
  `ModelReleaseStore.resolve_by_manifest_sha`.
- `build_analysis_package_zip(output, recording_name, dataset_name, runtime_descriptor, pipeline_definition, label_space)`.
- Delete `zoomspec_executor.py`; update imports in `runner.py` (already D3) and all tests.

**RED test:** update `test_zoomspec_remote_executor.py` to drive `PluginItemExecutor` with fake worker/registry/store; assert the same fail-closed checks and a valid envelope+zip. `test_remote_live_loop.py` uses the generic seam with fakes.

**Expected failure (RED):** tests still import the removed literal executor.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_remote_executor.py backend/tests/test_remote_live_loop.py backend/tests/test_remote_runner_work_wiring.py -v
```

**Commit checkpoint:** `refactor: move zoomspec remote execution onto generic plugin executor`

---

## TASK E3 — Record ZoomSpec CPU feasibility (not certified) + projections

**First: read before editing**
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/pipeline.py` (`device` parameterization).
- Spec §18 (Gate-0 evidence: ~42.3 s full CPU run, 14 vs 13 GPU-oracle detections).

**Interfaces:**
- ZoomSpec `PipelineDefinition.technical_execution_capabilities` includes `local_cpu/cpu/float32` (technical claim only).
- `execution_certificates.json` does **not** contain a ZoomSpec `local_cpu` certificate.
- Read-model `cpu_supported` stays `False` (legacy technical metadata; it never implies local runnability).

**RED test (`backend/tests/test_zoomspec_plugin.py` extension):**

```python
def test_zoomspec_declares_technical_cpu_but_not_certified(): ...
def test_zoomspec_cpu_supported_projection_false(): ...
```

**Expected failure (RED):** no technical CPU claim / projection logic.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_plugin.py -v
```

**Commit checkpoint:** `test: record zoomspec cpu feasibility without certification`

---

## TASK E4 — Golden regression sweep

**Goal:** prove no ZoomSpec scientific semantics changed.

**Commands:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_zoomspec_full_pipeline.py \
  backend/tests/test_zoomspec_cpn_detector.py \
  backend/tests/test_zoomspec_frn.py \
  backend/tests/test_zoomspec_ahlp.py \
  backend/tests/test_zoomspec_postprocess.py \
  backend/tests/test_zoomspec_lsstft_preprocessing.py \
  backend/tests/test_zoomspec_definition_registry.py -q
```

**Expected:** all pass unchanged. If any fails, do not adjust the frozen scientific modules; fix the integration layer.

**Commit checkpoint:** none (verification only; fix forward as separate commits if needed).

---

# PHASE F — Second Real Pipeline (CPN-only) + Genericity Proof

## TASK F1 — `cpn_bandwidth_tier_v1` label space + CPN-only plugin

**First: read before editing**
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/detector.py` (`CPNDetector`, `CPNProposal`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/preprocessing.py`.
- `label_spaces/spacenet_14.json` (format reference).

**Data (create `label_spaces/cpn_bandwidth_tier_v1.json`):**

```json
{ "id": "cpn_bandwidth_tier_v1", "version": 1,
  "classes": [ {"id": 0, "name": "narrow"}, {"id": 1, "name": "mid"}, {"id": 2, "name": "wide"} ] }
```

**Interfaces (create `backend/app/pipelines/cpn_bandwidth_tier/pipeline.py`):**

```python
class CPNBandwidthTierPipeline(Pipeline):
    def __init__(self, *, detector_checkpoint_path: Path, normalization, device: int | str):
        ...
    @property
    def definition(self) -> PipelineDefinition: ...
    def run(self, recording, parameters, workspace) -> PipelineOutput:
        # build_ls_stft_spectrogram(iq, ..., normalization=normalization, ...)
        # -> CPNDetector.detect_batch -> DetectionPayload(class_id=tier, class_name=tier_name)
        # No AHLP/FRN. Output label space = cpn_bandwidth_tier_v1.
```

The runtime requires LS-STFT normalization, so its ModelRelease/AssetManifest
MUST declare at least `detector_checkpoint` **and** `ls_stft_normalization`. The
CPN package owns its own AssetManifest V1 with two logical assets:

```json
{ "pipeline_id": "cpn_bandwidth_tier", "pipeline_version": "1.0.0",
  "assets": { "detector_checkpoint": "<sha256>", "ls_stft_normalization": "<sha256>" },
  "asset_manifest_sha256": "<self-hash computed at implementation time>" }
```

**Create `backend/app/pipelines/cpn_bandwidth_tier/plugin.py`:**

```python
PLUGIN = PluginDeclaration(
    definition=PipelineDefinition(
        id="cpn_bandwidth_tier", version="1.0.0",
        label_space="cpn_bandwidth_tier_v1",     # output label space (compatibility projection)
        output_label_space="cpn_bandwidth_tier_v1",
        input_compatibility=("spacenet_14",),    # accepts SpaceNet recordings
        dataset_adapters=("SpaceNet",),
        recommended_device="GPU", cpu_supported=False,
        stages=("ls_stft", "cpn", "bandwidth_tier"), inspectable_stages=(),
        task_capability="detection_classification",
        technical_execution_capabilities=(ExecutionCapability("remote_gpu", "cuda", "float16"),),
        recommended_execution="remote_gpu", model_release_required=True,
    ),
    runtime_factory_ref="app.pipelines.cpn_bandwidth_tier.plugin:build_runtime",
)
```

`build_runtime(*, assets, runtime_descriptor, output_label_space)` constructs
`CPNBandwidthTierPipeline` from the resolved assets + runtime descriptor and uses
the passed `output_label_space` (never resolving it itself). The CPN-only plugin
**reuses the frozen detector module** (`app.pipelines.zoomspec...detector`) as a
shared scientific dependency; it must not import control-plane modules.

**ModelRelease** `backend/app/pipelines/cpn_bandwidth_tier/model_releases/golden.json`
referencing the CPN package's own AssetManifest V1 (two logical assets:
`detector_checkpoint`, `ls_stft_normalization`) and its self-hash computed at
implementation time. `build_runtime` loads the normalization asset into an
`LSSTFTNormalization` and passes it to `CPNBandwidthTierPipeline`.

**RED test (`backend/tests/test_cpn_bandwidth_tier_plugin.py`, new):**

```python
def test_cpn_plugin_definition_input_output_split(): ...
def test_cpn_manifest_declares_detector_and_normalization_assets(): ...
def test_cpn_release_resolves_and_manifest_hash_matches(): ...
def test_cpn_pipeline_maps_tiers_to_label_space(): ...
def test_cpn_plugin_import_is_torch_free_at_declaration(): ...
```

**Expected failure (RED):** package/label space missing.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_cpn_bandwidth_tier_plugin.py -v
```

**Commit checkpoint:** `feat: add cpn bandwidth tier plugin`

---

## TASK F2 — Register CPN via config data + zero-control-plane-edit guard

**Interfaces:**
- Add `"app.pipelines.cpn_bandwidth_tier.plugin"` to `backend/app/pipelines/plugin_modules.json`.
- Add its default release to `model_release_defaults.json`.
- No edits to `plugin_registry.py`, `analysis/*`, or `remote_execution` core logic.

**RED test (`backend/tests/test_no_plugin_id_in_control_plane.py`, new):**

```python
CONTROL_PLANE = [
    "backend/app/analysis/service.py", "backend/app/analysis/worker.py",
    "backend/app/analysis/router.py",
    "backend/app/pipelines/registry.py", "backend/app/pipelines/plugin_registry.py",
    "backend/app/remote_execution/runner.py", "backend/app/remote_execution/coordinator.py",
    "backend/app/remote_execution/plugin_executor.py", "backend/app/remote_execution/package_publisher.py",
]

def test_cpn_id_absent_from_control_plane():
    for path in CONTROL_PLANE:
        assert "cpn_bandwidth_tier" not in Path(path).read_text()
        assert "zoomspec_yolo26n" not in Path(path).read_text()   # no plugin-id branching

def test_cpn_plugin_loaded_from_config_data(): ...
```

**Expected failure (RED):** if any plugin id is embedded in control-plane logic, the guard fails.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_no_plugin_id_in_control_plane.py -v
```

**Commit checkpoint:** `test: guard zero control-plane plugin-id branching`

---

## TASK F3 — Genericity proof: both plugins through one seam

**Interfaces:** no production code; composition tests only.

**RED test (`backend/tests/test_plugin_genericity.py`, new):**

```python
def test_plugin_registry_lists_both_plugins_without_core_branching(): ...
def test_generic_executor_accepts_both_plugin_declarations_with_fakes(): ...
def test_release_resolution_isolated_per_plugin_and_release(): ...
def test_asset_namespaces_do_not_collide_across_plugins(): ...
```

**Expected failure (RED):** only one plugin registered, or asset namespace collision.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_plugin_genericity.py -v
```

**Commit checkpoint:** `test: prove plugin genericity with second pipeline`

---

# FINAL GATES

## GATE 1 — CPU-only full verification (run first, no GPU)

Run all of the following on the implementation branch; all must pass.

```bash
# backend full regression
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q

# control-plane / runner import boundary
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" - <<'PY'
import sys, importlib
for mod in ("app.pipelines.registry", "app.pipelines.plugin_registry",
            "app.remote_execution.runner", "app.remote_execution.runtime",
            "app.analysis.service", "app.main"):
    importlib.import_module(mod)
assert "torch" not in sys.modules, "torch leaked into control plane"
assert "ultralytics" not in sys.modules, "ultralytics leaked into control plane"
print("control-plane import boundary OK")
PY

# golden manifest identity
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -c "from pathlib import Path; from app.remote_execution.assets import load_pipeline_asset_manifest; print(load_pipeline_asset_manifest(Path('backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json')).asset_manifest_sha256)"

# frontend unit + build
cd frontend && npm run test -- --run && npm run build
```

Expected:
- backend suite green including all ZoomSpec golden tests;
- import boundary prints OK; golden hash prints `16cc0534...`;
- frontend vitest green and `tsc -b && vite build` succeeds.

## GATE 2 — Consolidated Live GPU Acceptance (describe; DO NOT RUN in this plan)

Executed once after A–F, per spec §21.1. It is the only place a new runtime pin replaces the historical accepted baseline `5bb5be4` (which remains the historical accepted runtime).

```text
1. Real SSH probe: remote runtime reachable; pinned runtime commit, namespaced assets,
   SpaceNet root, and descriptor CUDA device index valid.
2. ZoomSpec golden remote inference: existing frozen semantics; run through the generic
   PluginItemExecutor + ModelRelease(golden) path.
3. CPN-only remote inference: through the same generic plugin/executor seam
   (remote_gpu technical capability; output label space cpn_bandwidth_tier_v1).
4. Package <-> DB parity for both runs: detections, label spaces, execution projection,
   and provenance (model_release_id, asset_manifest_sha256, runtime commit).
5. Appropriate Algorithm Lab verification on the completed runs.
```

Rules:
- If A–F did not touch coordinator/recovery/transport, the full M9.1 Gate-B suite need not be rerun mechanically; if they were touched, escalate the acceptance scope.
- Record any `ExecutionCertificate` created by the gate and the new runtime pin in the acceptance evidence.
- `5bb5be4` is not modified, deleted, or reused as the M9.2 runtime.

---

## Spec Coverage Matrix

| Spec section | Tasks |
|---|---|
| §5.1 Plugin declaration | A2, A3 |
| §5.2 PluginVersion (opaque) | A1, A3 |
| §5.3 ModelRelease vs PluginVersion | B1, B5, E1 |
| §5.4/§9.2 AssetManifest V1 frozen | B2, B5, F1 |
| §5.5/§10 DatasetAdapter | C1, C2 |
| §5.6/§8.4/§9/§18 Certification | A3, B5, D1, D2, E3 |
| §5.7/§8.2/§10.3/§17 input vs output label space | C4, F1 |
| §8.5 registry + lazy factory | A2, A3 |
| §9.5 resolved default release | B1, B4, B5 |
| §11.1 executor kinds / separate worker | D2 |
| §11.2 executor registry / local+remote symmetry | D2, D2B |
| §11.3 RuntimeDescriptor + public projection | D1, D5 |
| §11.4 namespaced trusted paths + probe | D4 |
| §11.5 generic PluginItemExecutor | D3A/D3B, E2 |
| §12 control-plane torch-free | A3, F2, GATE 1 |
| §13 error codes | A2, B1, C1, C4, D1, D2 |
| §14 provenance invariants | B3, D5 |
| §16 migration | E1–E4 |
| §17 second pipeline | F1–F3 |
| §21.1 live gate | GATE 2 |

---

## Self-Review

**Spec coverage:** all fourteen spec sections plus §21.1 map to tasks in the matrix above; the four decoupling principles are exercised end-to-end by F3.

**Placeholder scan:** no `TODO`/`TBD`/`FIXME`; every task names exact files, interfaces, RED tests, GREEN commands, and a commit checkpoint. The only deferred value is the CPN AssetManifest self-hash, which is necessarily computed at implementation time and is explicitly noted in F1.

**Type/signature consistency:**
- `PipelineDefinition` extensions (A1) are consumed by A2/A3, B1/B4/B5, C4, D1/D2, E1, F1.
- `ExecutionCapability` (A1) is consumed by D1 (`certified_capabilities(runtime_ref=...)`), A3 (ZoomSpec remote_gpu) and F1 (CPN remote_gpu).
- `PluginRuntime.execute(recording, parameters, workspace)` and
  `PluginRuntimeFactory(*, assets, runtime_descriptor, output_label_space)` (A2)
  are consumed by D2B (local), D3 (remote), E1, F1. Both call
  `PluginHandle.load_runtime(*, assets, runtime_descriptor, output_label_space)`
  (never `PluginRegistry.load_runtime`), with the executor resolving the output
  `LabelSpace` first.
- `RuntimeDescriptor` (D1) exposes private `environment_ref` + public `environment_label`; `public_projection()` returns only `environment_label`. Consumed by D4 (probe), D5 (publisher/ingestor), D2 (providers), D2B (local worker), D3 (remote executor).
- `ExecutionCertificate.runtime_ref` is part of `key()`; `is_certified` and `certified_capabilities` require an exact `runtime_ref`. Remote refs bind `required_remote_runtime_commit`; local refs are operator-owned generation labels.
- `ModelReleaseStore.resolve_by_manifest_sha` (B1) is consumed by D2B/D3/E2, while new runs also carry the exact `model_release_id` on the wire (B3).
- `ResolvedRecordingInput` (C1) is consumed by D3 and re-exported via resolver (C2).
- `LocalInferenceWorkerProvider` (D2) takes the configured interpreter
  (`Settings.local_cpu_python_path` / `local_gpu_python_path`) and immutable
  `runtime_ref`; it launches `app.analysis.local_inference_worker` (D2B) and
  never uses `sys.executable` or `app.analysis.worker`.

**Dependency order:** A→B→C→D→E→F. Within B, B5 (golden release/default) precedes Phase D so D1's seed certificate is never dangling. A3 declares ZoomSpec technical capability before D2 activates certificate-driven projections. D2B (local worker) precedes E2 so local and remote share the plugin contract before ZoomSpec migrates. C3/C4 consume A2. **Within D: D3A → D4 → D5 → D3B** — D3A (generic executor core, injected seams) consumes A3+B1+B5+C1; D4 supplies the namespaced worker context + deployment RuntimeDescriptor; D5 supplies the descriptor-driven package publisher/ingestor; D3B performs the final `runner._cli_work` cutover and is the only task that removes the ZoomSpec literal dispatch. Until D3B, the ZoomSpec remote production path stays intact. E consumes D; F consumes E. No forward dependency.

**YAGNI:** no model registry service, no multi-GPU scheduler, no release UI, no streaming/CDN, no vendor abstraction. `local_gpu` is declared but not required to be implemented (matches spec); `local_cpu` requires an explicitly configured interpreter + runtime ref and is unavailable otherwise. M9.3 excluded.

**Provenance preservation:**
- B2 locks the V1 manifest payload and golden hash.
- B3 adds optional `model_release_id` to the hashed request wire but omits it when `None`, so `build_batch()` reproduces pre-M9.2 `request_sha256` byte-exactly (asserted against the TASK 0 fixture); new runs hash/transmit/verify the exact release id.
- B1 validates `asset_manifest_path` containment inside the plugin package; asset file paths remain deployment-only.
- D5 validates package execution against the run's persisted descriptor projection, with a legacy `remote_gpu -> cuda:0` fallback.
- D1/D2 bind certificates to `runtime_ref`, so a runtime commit or local environment-generation change invalidates the old certificate.
- No task weakens runtime-commit, fingerprint/source-hash, terminal-immutability, or physical-box/label checks.

**Control-plane edit guard:** F2's guard test is the objective proof that adding a plugin required no control-plane logic edits; `plugin_modules.json`, `model_release_defaults.json`, and `execution_certificates.json` are configuration data, not logic.

**Coordinator/recovery/transport:** untouched. D3B changes only `runner._cli_work` construction; D4/D5 add the generic worker context/probe/package seams; D2 adds providers around existing `SshRemoteExecutorProbe`/`CoordinatorJobManager` without changing their internals, and adds a new `LocalInferenceWorkerProvider` + `local_inference_worker` rather than altering `LocalJobManager` or `app.analysis.worker`.

**Required-fix coverage (architect reviews):**
1. `environment_ref` private / `environment_label` public — D1 + tests.
2. `runtime_ref` in certificate matching — D1, D2 + tests.
3. configured local inference interpreter (not `sys.executable`) — D2 + `config.py` + tests.
4. `model_release_id` wire identity with `None` omission — B3 + TASK 0 fixture + tests.
5. CPN assets include `ls_stft_normalization` — F1 + tests.
6. phase ordering (ZoomSpec caps in A3, golden release in B5, D1 non-dangling) — A3, B5, D1.
7. non-expiring implementation start SHA — TASK 0.
8. unified `PluginHandle.load_runtime(*, assets, runtime_descriptor, output_label_space)` — A2, A3, D2B, D3, E1, F1 + signature tests.
9. plugin-native local inference worker (shared contract) — D2B + D2 launch + tests.
10. hardened `runtime_ref` (remote commit-bound; local immutable generation) — D1, D2 + tests.

---

## Execution Handoff

Plan complete. Phase order A→F, then GATE 1 (CPU-only) and GATE 2 (live GPU, described only). Do not begin implementation until the plan is approved.

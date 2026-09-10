# M9.2 Inference Plugin & Execution Framework — Design Specification

Date: 2026-09-10
Status: Proposed design specification (design only; no production implementation)
Base: `feature/v1-core @ 55f537b640fff095d51f32b92dfda9c859ab2839`
Scope: Design specification only. No production code, tests, runtime, or GPU changes.

> This specification turns WISA from a one-model integration into a multi-model
> inference platform. It **freezes** the proven M9.1 remote control plane and
> introduces a Plugin / ModelRelease / Executor abstraction around it. It refines
> `2026-09-06-algorithm-integration-standard-v1-design.md` and
> `2026-09-09-m9-1-task12f-remote-platform-integration-design.md`; where they
> conflict, this specification is authoritative for M9.2.

---

## 1. Purpose

WISA currently has exactly one deep-learning inference path: the frozen ZoomSpec
pipeline `zoomspec_yolo26n_aug_combined_frn_v3`, dispatched by literal imports in
`backend/app/remote_execution/runner.py:645-648` and
`backend/app/remote_execution/zoomspec_executor.py`.

Every additional model currently requires edits to shared control-plane files:
`backend/app/pipelines/registry.py`, `backend/app/analysis/service.py`,
`backend/app/remote_execution/{worker_context,zoomspec_executor,package_publisher,probe}.py`,
`backend/app/remote_execution/result_ingestor.py`, and repo paths in
`backend/app/main.py`. That is the integration friction M9.2 removes.

M9.2 defines a **Plugin Framework** with four separation-of-concerns
principles:

```text
1. Same PluginVersion + new retrained weights => new ModelRelease/assets only.
   No platform code change.
2. New architecture/pipeline => new or updated Plugin only.
   No control-plane change.
3. New execution environment => executor/runtime config only.
   No scientific Plugin change.
4. New dataset/source => DatasetAdapter only.
   No Plugin / executor-core change.
```

M9.2 does not add new models itself, except the mandatory second-pipeline gate
(CPN-only/YOLO baseline) that proves genericity.

---

## 2. Frozen Contracts

M9.2 MUST NOT change the semantics, identity, or persistence of the following.
All M9.2 work is additive around them.

| Frozen contract | Owner (unchanged) |
|---|---|
| `AnalysisRun` lifecycle and terminal immutability | `backend/app/analysis/` |
| Coordinator subprocess, fencing token, restart/reconciliation | `backend/app/remote_execution/coordinator.py`, `recovery.py` |
| SSH transport (`shell=False` fixed argv, strict host-key, no request-controlled paths) | `backend/app/remote_execution/{transport,profile}.py` |
| Canonical request identity and hashing scheme | `backend/app/remote_execution/canonical.py` |
| `asset_manifest_sha256` as the cryptographic asset identity | `backend/app/remote_execution/assets.py` |
| Atomic result publish + write-once terminal verification | `backend/app/remote_execution/runner.py` |
| `ingest_remote_result` identity checks + single persistence path | `backend/app/remote_execution/result_ingestor.py`, `validation.py` |
| `AnalysisResultWriter` (physical-box + label validation) | `backend/app/remote_execution/validation.py` |
| Analysis Package v1 (`schema_version=1`, `extra="forbid"`) | `backend/app/imported_runs/schema.py` |
| Algorithm Lab, Signals, Signal Detail, evaluation read models | `backend/app/{evaluation,detections,benchmarks}/`, `frontend/` |
| ZoomSpec LS-STFT / CPN / AHLP / FRN / postprocess numerics | `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/` |

The hashing **scheme** is frozen. The set of provenance fields MAY grow
additively as described in §14 and §16; no persisted completed run is ever
re-hashed or rewritten.

---

## 3. Goals

1. Define a **Pipeline Plugin** identity and an immutable **PluginVersion**.
2. Define a separate immutable **ModelRelease** identity for
   checkpoint/config/normalization changes under an unchanged PluginVersion.
3. Define a **generic AssetManifest** with plugin-declared logical asset names.
4. Define the **DatasetAdapter -> RecordingInput** boundary.
5. Define a declarative **Plugin parameter schema** and **execution
   capabilities**.
6. Define an **executor/runtime abstraction** for `local_cpu`, `local_gpu`, and
   `remote_gpu`.
7. Keep the control plane **torch/ultralytics-free**; heavy ML imports stay lazy
   and execute only inside a plugin runtime.
8. Replace the remote **ZoomSpec literal ItemExecutor dispatch** with a generic
   plugin/executor registry seam.
9. Replace the hardcoded `cuda:0` / device-0 assumption with a **Runtime
   Descriptor**.
10. Migrate ZoomSpec into the framework as the **golden reference plugin**
    without changing its scientific semantics.
11. Require **certification**, not mere technical executability, before a
    device/executor is offered for a plugin release.
12. Add a **second real pipeline gate** (preferred candidate: CPN-only/YOLO
    baseline) that proves a new pipeline integrates as a Plugin only.

---

## 4. Non-Goals

M9.2 does NOT build:

- a training platform, training loop, or dataset cache builder;
- a model registry **service** (the ModelRelease store is in-repo config/data,
  not a running service);
- a multi-GPU scheduler or GPU vendor abstraction;
- streaming / realtime SDR;
- a CDN, artifact object store, or remote artifact fetch protocol;
- automatic cross-plugin execution-policy selection;
- batch evaluation across multiple pipelines (M9.3);
- a second `DetectionResult` persistence path;
- a second result/package schema.

---

## 5. Terminology

### 5.1 Pipeline Plugin

The unit of integration. A Plugin is a distribution that declares:

- a stable `plugin_id`;
- one or more immutable PluginVersions;
- a `PluginDefinition` (capabilities + metadata, torch-free);
- a parameter schema;
- execution capabilities / certification status;
- zero or more `ModelRelease`s;
- zero or more `DatasetAdapter` bindings;
- the scientific implementation modules (heavy imports lazy).

A Plugin is **not** the same as a Pipeline run. The existing `Pipeline` ABC in
`backend/app/pipelines/base.py` remains the scientific execution interface; the
Plugin is the packaging + identity + declaration layer around it.

### 5.2 PluginVersion

`(plugin_id, plugin_version)` where `plugin_version` is an immutable code +
behavior contract. Changing algorithm code, architecture, coupling between
stages, output semantics, or the parameter schema requires a new PluginVersion.

### 5.3 ModelRelease

`(plugin_id, plugin_version, model_release_id)` identifies an immutable set of
trained weights / config / normalization. A new checkpoint, retrain, fine-tune,
or normalization change under the same architecture produces a new ModelRelease
with the same PluginVersion. Its cryptographic binding is the
`asset_manifest_sha256`.

### 5.4 AssetManifest

A generic, plugin-declared mapping from **logical asset names** to SHA256
identities for one ModelRelease. Logical names are defined by the plugin (for
example `detector_checkpoint`, `frn_checkpoint`, `ls_stft_normalization`,
`frozen_config`), not by the platform. The manifest self-hash excludes itself.

### 5.5 DatasetAdapter

The only component allowed to read a dataset-specific on-disk layout and map it
to a platform `RecordingInput`. Plugins and executors never parse dataset files
directly.

### 5.6 ExecutionCapability

A declared, certified-able execution target: `local_cpu`, `local_gpu`,
`remote_gpu`. A capability is a claim subject to certification (§18); declaring
it does not activate it.

### 5.7 RuntimeDescriptor

A concrete execution instance description (`executor`, `device_type`,
`device_index`, `precision`, `environment_ref`) that replaces device literals
such as `cuda:0` and `_DEVICE_INDEX = 0`.

### 5.8 ExecutionCertificate

Evidence record that a `(plugin, plugin_version, model_release, capability,
device_type)` combination has passed an acceptance gate. A capability without a
certificate is not offered by the platform.

---

## 6. Architecture Overview

### 6.1 Component layers

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Frontend (generic, capability-driven)                                 │
│  SpectrumAnalysisPage / executorPolicy / PipelineDefinition types     │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ read model (extended additively)
┌───────────────────────────────▼──────────────────────────────────────┐
│ Local control plane  (torch/ultralytics-free)                         │
│  PluginRegistry        ModelReleaseStore      ExecutorRegistry         │
│  PipelineRegistry      AssetManifest loader   Availability probes     │
│  AnalysisService  request_builder  canonical  result_ingestor          │
│  coordinator  recovery  AnalysisResultWriter  API read models         │
└───────────────┬───────────────────────────────────┬──────────────────┘
                │ local_cpu / local_gpu             │ remote_gpu (SSH)
                ▼                                   ▼
┌───────────────────────────────┐   ┌───────────────────────────────────┐
│ Local plugin runtime           │   │ Remote plugin runtime             │
│  PluginExecutor registry       │   │  runner work: PluginItemExecutor  │
│  lazy ML imports               │   │  PluginRegistry + ExecutorRegistry │
│  DatasetAdapter -> RecordingIn │   │  DatasetAdapter -> RecordingInput  │
└───────────────────────────────┘   └───────────────────────────────────┘
```

### 6.2 Request/data flow (remote, generic)

```text
create_run(recording, plugin_id, plugin_version, model_release_id, executor, params)
  -> PluginRegistry.get(plugin_id, plugin_version)            # torch-free
  -> validate params against plugin parameter schema         # torch-free
  -> resolve ModelRelease + AssetManifest (pack+release)     # torch-free
  -> select ExecutorProvider for `executor`
  -> RuntimeDescriptor built by provider
  -> freeze provenance (request_sha256, model_release_id, asset_manifest_sha256)
  -> launch coordinator (remote) or worker (local)
       remote: runner resolves PluginItemExecutor for plugin_id
               -> DatasetAdapter resolves RecordingInput
               -> AssetManifest resolution by (plugin_id, version, manifest_sha)
               -> PipelineExecution(... RuntimeDescriptor ...)
               -> PipelineOutput
               -> Analysis Package v1 zip + envelope (write-once)
       local:  same scientific path without SSH
  -> ingest -> AnalysisResultWriter -> SAME AnalysisRun
```

### 6.3 Invariant

The platform core never branches on a concrete plugin id. The only mappings from
`plugin_id` to behavior are plugin declarations loaded through a registry seam.

---

## 7. Ownership Boundaries

| Concern | Owner | Must not do |
|---|---|---|
| Plugin identity, definition, parameters, assets, capabilities | Plugin author | import torch at control-plane import time; edit control-plane code |
| Scientific inference | Plugin runtime | read DB, SSH, or dataset files directly |
| Plugin discovery + validation | Platform `PluginRegistry` | embed plugin-specific constants |
| ModelRelease + AssetManifest resolution | Platform `ModelReleaseStore` | hardcode asset names or paths |
| Execution selection + RuntimeDescriptor | Platform `ExecutorRegistry` | hardcode `cuda:0` / device 0 |
| Dataset resolution | `DatasetAdapter` implementer | expose GroundTruth to inference |
| AnalysisRun lifecycle / provenance / ingest | Platform control plane | change frozen M9.1 semantics |
| Remote transport | Platform `SshRunner` | accept request-controlled paths/secrets |
| Read model + UI | Platform frontend | hardcode plugin ids or device strings |

---

## 8. Plugin Contract

### 8.1 Identity

```text
plugin_id:      ^[a-z0-9][a-z0-9_]{0,127}$     (stable, global within platform)
plugin_version: ^[0-9]+\.[0-9]+\.[0-9]+$       (semver)
plugin_api_version: integer (M9.2 = 1)
```

`plugin_id` replaces the current `PipelineDefinition.id` as the primary identity.
The existing read-model field `id` remains an alias for `plugin_id` so the
frontend and API contracts do not break.

### 8.2 PluginDefinition (extended)

The runtime `PipelineDefinition` (backend) and `PipelineDefinitionRead`
(API/frontend) are extended additively:

```text
plugin_id            (= existing `id`)
plugin_version       (= existing `version`)
plugin_api_version   (new)
name
label_space
task_capability      (detection_localization | detection_classification)
stages
inspectable_stages
parameter_schema     (new; JSON Schema object; {} means no parameters)
execution_capabilities   (new; ordered tuple)
recommended_execution    (new; one of the capabilities)
model_release_required   (new; bool)
dataset_adapters         (new; tuple of adapter ids)
```

`executors_supported` and `recommended_executor` remain in the read model as
derived compatibility projections of `execution_capabilities` /
`recommended_execution`.

### 8.3 Parameter schema

Each plugin declares a JSON Schema object for `parameters`. M9.2 supports the
subset: `type`, `properties`, `required`, `additionalProperties=false`,
`enum`, numeric `minimum`/`maximum`, and `default`. The control plane validates
parameters before run creation **without importing plugin scientific code**.
Frozen plugins declare an empty object schema. This replaces the hardcoded
`"parameters": {}` in `backend/app/remote_execution/request_builder.py:140`.

### 8.4 Execution capabilities

A plugin declares which executors it can run on:

```text
execution_capabilities = (
  ExecutionCapability(
    executor="remote_gpu",
    device_type="cuda",
    precision="float16",
    certified=True,
    certificate_ref="...",
  ),
  ExecutionCapability(
    executor="local_cpu",
    device_type="cpu",
    precision="float32",
    technically_feasible=True,
    certified=False,
  ),
)
```

A declared-but-uncertified capability is visible as metadata but MUST NOT be
offered as runnable. `recommended_execution` MUST reference a certified
capability.

### 8.5 Plugin registry seam

```text
PluginRegistry
  .get(plugin_id, plugin_version) -> PluginHandle (definition + factory ref)
  .list() -> [PluginDefinition]
```

- The registry loads plugin **declarations** only. It MUST be importable with no
  torch/ultralytics (`test_zoomspec_definition_registry.py` boundary preserved).
- Discovery is declarative: a plugin registry configuration lists dotted module
  paths (built-in reference plugins are listed by default). Adding a plugin
  appends a dotted path to configuration, not logic to `registry.py`.
  Out-of-tree plugins MAY use an `importlib.metadata` entry-point group
  (`wisa.pipeline_plugins`); this is optional packaging, not required for M9.2.
- The scientific `Pipeline` is constructed only by a plugin runtime inside a
  workspace (local worker or remote executor), never by the control plane.

---

## 9. ModelRelease and AssetManifest

### 9.1 ModelRelease identity

```text
model_release_id: ^[a-z0-9][a-z0-9_.-]{0,127}$
(plugin_id, plugin_version, model_release_id) is immutable.
```

A ModelRelease records:

```text
model_release_id
plugin_id / plugin_version
asset_manifest (see §9.2)
created_at (informational)
notes (informational, e.g. training run reference)
```

Changing weights/config/normalization creates a **new ModelRelease**, not a new
PluginVersion. Changing code or architecture creates a **new PluginVersion**.

### 9.2 Generic AssetManifest

```json
{
  "plugin_id": "zoomspec_yolo26n_aug_combined_frn_v3",
  "plugin_version": "1.0.0",
  "model_release_id": "golden-2026-08",
  "assets": {
    "detector_checkpoint": "<sha256>",
    "frn_checkpoint": "<sha256>",
    "frozen_config": "<sha256>",
    "ls_stft_normalization": "<sha256>"
  },
  "asset_manifest_sha256": "<self-excluding canonical hash>"
}
```

The canonical payload (excluding `asset_manifest_sha256`) adds
`model_release_id` to the M9.1 payload. The hashing and self-exclusion rules in
`backend/app/remote_execution/assets.py` are preserved; only the field set grows.
Logical asset names are free-form strings validated as non-empty identifiers.

### 9.3 Resolution and verification

- Local control plane resolves `(plugin_id, plugin_version)` -> plugin
  declaration -> release directory -> manifest. The selected release's
  `asset_manifest_sha256` is frozen into run provenance.
- Remote runtime resolves the same manifest by `(plugin_id, plugin_version,
  asset_manifest_sha256)` and verifies:
  - the manifest exists for the plugin/version;
  - its self-hash equals the request's `asset_manifest_sha256`;
  - its `model_release_id` equals the request's `model_release_id`;
  - each asset file byte-hash matches the manifest;
  - the deployed runtime commit matches the required commit.
- Asset file paths are deployment configuration (executor side), never wire
  input. See §12.4.

### 9.4 Same-architecture retraining rule

```text
new retrained checkpoint
  -> new ModelRelease manifest (new asset_manifest_sha256)
  -> new AnalysisRun records new model_release_id
  -> PluginVersion unchanged; platform code unchanged
```

This requires version-scoped manifest resolution (§9.3) and removal of the
hardcoded manifest path currently in `backend/app/main.py:30-35` and
`backend/app/remote_execution/worker_context.py:110-114`.

---

## 10. DatasetAdapter -> RecordingInput Boundary

### 10.1 Contract

```text
DatasetAdapter
  .dataset_name: str
  .resolve(split, key, label_space, identity_requirements) -> ResolvedRecordingInput
```

`ResolvedRecordingInput` carries:

```text
recording_fingerprint
source_data_sha256
RecordingInput  (existing dataclass)
```

### 10.2 Rules

- A DatasetAdapter is the only component that reads dataset layout.
- It MUST verify the same double identity as today
  (`recording_fingerprint_v1` + exact raw `source_data_sha256`).
- GroundTruth is inspected only for fingerprint verification and MUST NOT reach
  inference.
- Adapters are selected by `dataset_name`; `resolve_space_net` becomes the
  built-in `SpaceNetAdapter` implementation of this interface.
- The local path already satisfies the boundary: the Recording model is mapped
  to `RecordingInput` in `backend/app/analysis/worker.py`.

### 10.3 Plugin binding

A plugin declares `dataset_adapters` (for example `("spacenet",)`). The
executor resolves the adapter by `recording.dataset_name`; a plugin that does
not support the dataset fails closed before model construction.

---

## 11. Executor / Runtime Abstraction

### 11.1 Executor kinds

| Executor | Where it runs | Heavy deps | Selection |
|---|---|---|---|
| `local_cpu` | In-process/worker on the API host | none required beyond plugin deps | capability must be certified |
| `local_gpu` | Local worker on a GPU host | torch/CUDA | capability must be certified |
| `remote_gpu` | Detached remote worker over SSH | torch/CUDA/ultralytics | asset + runtime probe required |

`local_gpu` is introduced by the abstraction but is **not required to be
implemented** in M9.2; it exists so the framework does not assume all GPU work
is remote.

### 11.2 ExecutorRegistry

```text
ExecutorRegistry
  .provider(executor_name) -> ExecutorProvider
  .capabilities(plugin_definition) -> [ExecutionCapability]
  .availability(plugin_definition, runtime_descriptor, recording) -> Availability
```

`AnalysisService` dispatches by capability, not by the literal string
`remote_gpu` (`backend/app/analysis/service.py:147,191`). The existing
`RemoteExecutorProbe` protocol is one `ExecutorProvider` implementation.

### 11.3 RuntimeDescriptor

```text
RuntimeDescriptor
  executor:       local_cpu | local_gpu | remote_gpu
  device_type:    cpu | cuda
  device_index:   int | None
  precision:      float32 | float16
  environment_ref: str | None
```

- Built by the selected `ExecutorProvider` (from `RemoteProfile` for remote,
  from configuration for local).
- Passed to plugin pipeline construction instead of integer `0`.
- Serialized into `Analysis Package` `ExecutionMetadata` (executor, device,
  environment) and persisted in `execution_metadata_json`.
- `backend/app/remote_execution/result_ingestor.py:150` MUST validate the
  package execution metadata against the run's persisted `RuntimeDescriptor`,
  not the literal `cuda:0`.
- `backend/app/remote_execution/probe.py` checks readiness for the descriptor's
  device type/index (CPU always ready; CUDA requires availability of that
  index).

### 11.4 Remote asset + runtime context

`RemoteWorkerContext` is generalized:

- required fields become `repo_root`, `job_root`, `required_runtime_commit`,
  `manifest_root`, and an **asset path configuration** validated as absolute
  safe POSIX paths;
- the fixed four ZoomSpec env-var names are replaced by plugin-declared logical
  asset names resolved through the asset path configuration;
- it still carries **no** SSH/credential reference and no request-controlled
  path;
- parsing still fails closed on any missing/unsafe field.

### 11.5 ItemExecutor / Plugin registry seam

`backend/app/remote_execution/runner.py:645-648` currently hardcodes
`ZoomSpecRemoteItemExecutor`. M9.2 replaces this with:

```text
PluginItemExecutor (generic)
  execute(item, job_root):
    plugin   = PluginRegistry.get(batch.pipeline.id, batch.pipeline.version)
    executor = ExecutorRegistry.provider(batch.runtime.executor)
    adapter  = DatasetAdapterRegistry.get(item.recording.dataset_name)
    runtime  = executor.runtime_descriptor
    assets   = ModelReleaseStore.verify(...)
    resolved = adapter.resolve(...)
    output   = plugin.execute(resolved.recording_input, item.parameters, workspace, runtime, assets)
    publish(output, ...)
```

The generic executor owns verification/orchestration; the plugin owns science.
ZoomSpec becomes one registered plugin, not a special case.

---

## 12. Control-plane / Runtime Separation

- The control plane (`backend/app/analysis`, `backend/app/pipelines` registry
  declarations, `backend/app/remote_execution` except the lazy `work`/`probe`
  bodies, `backend/app/main.py`) MUST remain torch/ultralytics-free.
- Plugin declaration modules MUST import without torch. Heavy imports are lazy
  inside execution methods (the pattern already used by
  `zoomspec.../detector.py`, `frn.py`, `preprocessing.py`).
- The runner module import MUST remain GPU-library-free; lazy imports stay
  inside `_cli_probe` / `_cli_work`.
- Enforced by import-boundary tests (existing precedent:
  `backend/tests/test_zoomspec_definition_registry.py:57` and
  `backend/tests/test_remote_runner_work_wiring.py:120`).

---

## 13. Error Handling

All failures are explicit, typed `PlatformError` codes, fail-closed, and never
crash the control plane. New/renamed codes:

| Code | Trigger |
|---|---|
| `PLUGIN_NOT_FOUND` | unknown `plugin_id`/`plugin_version` |
| `PLUGIN_API_INCOMPATIBLE` | plugin API version unsupported |
| `PLUGIN_PARAMETERS_INVALID` | parameters violate plugin schema |
| `MODEL_RELEASE_NOT_FOUND` | release unknown for plugin version |
| `MODEL_RELEASE_MISMATCH` | remote release/manifest hash disagreement |
| `EXECUTION_CAPABILITY_UNAVAILABLE` | executor not certified/available for plugin |
| `RUNTIME_DESCRIPTOR_INVALID` | malformed/unsupported descriptor |
| `DATASET_ADAPTER_NOT_FOUND` | no adapter for `dataset_name` |
| `DATASET_ADAPTER_INCOMPATIBLE` | adapter cannot satisfy plugin dataset binding |
| `REMOTE_WORKER_CONTEXT_INVALID` | existing; extended for generic asset config |

Existing M9.1 codes (`PIPELINE_ASSET_MISMATCH`,
`REMOTE_IMPLEMENTATION_MISMATCH`, `REMOTE_RESULT_INVALID`,
`REMOTE_RESULT_CONFLICT`, `EXECUTOR_UNAVAILABLE`, `PIPELINE_INCOMPATIBLE`) remain
valid with unchanged meaning.

---

## 14. Provenance Invariants

M9.2 MUST preserve every M9.1 provenance guarantee and add ModelRelease
provenance without weakening any of them.

1. The canonical request identity scheme (`compute_request_sha256` over the
   canonical payload) is unchanged.
2. `asset_manifest_sha256` remains the cryptographic asset identity and is
   verified end-to-end (control plane, probe, remote executor, ingest).
3. `plugin_id` and `plugin_version` remain frozen into the request, envelope,
   package, and run.
4. `model_release_id` is added to the frozen request metadata and the remote
   envelope, and is verified against the manifest's own `model_release_id`.
5. A completed AnalysisRun is immutable. Ingest is idempotent for equal payload
   SHA and conflicts otherwise.
6. Runtime commit verification remains mandatory before inference.
7. Physical-box and label validation remain mandatory on ingest.
8. `failed`/`interrupted`/`completed` runs are never overwritten.
9. Parameters are frozen into provenance after schema validation; no
   post-freeze mutation.
10. GroundTruth never reaches inference.

### 14.1 Wire compatibility

- Analysis Package v1 is unchanged: `schema_version=1`, `extra="forbid"`.
  `model_release_id` is **not** added to the public package.
- The internal remote wire models MAY gain additively:
  - `RemotePipelineRefV1.model_release_id: str | None = None`;
  - `RemoteExecutionEnvelopeV1.model_release_id: str | None = None`.
- The remote wire `schema_version` remains `1` while additions are optional.
  A future breaking change increments it; mixed-version control-plane/remote
  operation is explicitly unsupported (the runtime commit is pinned and
  upgraded atomically).

---

## 15. Compatibility Rules

1. `PipelineDefinitionRead` additions are optional so an older frontend keeps
   working; the current `executors_supported` / `recommended_executor` fields
   are retained as projections.
2. `PluginDefinition.id` remains the API-visible identity; existing
   `AnalysisRun.pipeline_id` values are unchanged.
3. Existing completed ZoomSpec runs remain valid. Their `model_release_id` is
   derived from the already-persisted `asset_manifest_sha256` during M9.2E and
   recorded for new runs; historical rows are not rewritten.
4. Algorithm Integration Standard v1 §14 (checkpoint change => new Pipeline
   version) is refined by this spec: under the plugin framework, a checkpoint/
   config/normalization change is a new **ModelRelease**, not necessarily a new
   PluginVersion. This is the single deliberate semantic refinement of the v1
   standard and MUST be reflected in the standard's next revision note.
5. Analysis Package v1, benchmark manifests, imported-batch identities, and
   Algorithm Lab comparisons remain unchanged.

---

## 16. Migration Strategy (ZoomSpec golden plugin)

The default response to heterogeneity is: **write a Plugin, do not change the
control plane.**

| Step | Change | Rollback risk |
|---|---|---|
| 1 | Introduce `PluginDefinition`/registry types additively; existing ZoomSpec definition maps onto them | none (behavior unchanged) |
| 2 | Wrap ZoomSpec scientific modules behind the plugin declaration; keep numeric constants and orchestration identical | low; covered by existing ZoomSpec tests |
| 3 | Seed one ModelRelease from the current `asset_manifest.json`; preserve all four logical names and SHAs | low |
| 4 | Move remote executor dispatch to the generic PluginItemExecutor registry seam | medium; covered by `test_remote_runner_work_wiring.py` |
| 5 | Generalize worker context assets + RuntimeDescriptor; validate ingest against persisted descriptor | medium; covered by remote result tests |
| 6 | Add the CPN-only second plugin (§17) | low |
| 7 | Record ZoomSpec CPU status as technically feasible / not certified (§18) | none |

No step changes ZoomSpec inference numerics. Golden tests
(`test_zoomspec_full_pipeline.py`, `test_zoomspec_cpn_detector.py`,
`test_zoomspec_frn.py`, `test_zoomspec_ahlp.py`,
`test_zoomspec_postprocess.py`, `test_zoomspec_lsstft_preprocessing.py`) remain
authoritative.

---

## 17. Second Real Pipeline Gate

M9.2 MUST integrate one additional real pipeline through the plugin seam.
Preferred candidate: **CPN-only / YOLO baseline**.

Rationale:

- Reuses the already-frozen detector stage and existing checkpoint, so no new
  trained model is required.
- Exercises a different pipeline composition (detector -> physical proposals ->
  postprocess) without AHLP/FRN.
- Proves Plugin-only integration and executor genericity.
- Provides a useful `detection_localization` baseline for Algorithm Lab.

Candidate ranking (from Gate 0):

1. CPN-only/YOLO baseline (recommended second gate).
2. STFTEnergy (`local_cpu`, scipy-only, no assets) — useful executor-path probe.
3. Dummy — control-plane smoke only.
4. A genuinely new architecture (RT-DETR/D-FINE) — deferred until the seam is
   proven; highest effort/risk.

Acceptance for the gate: the second pipeline is added by creating its plugin
package + ModelRelease manifest/config and registering it; no edits to
control-plane logic (asset hashing, ingest, lifecycle, transport, Algorithm Lab)
are permitted.

---

## 18. CPU Certification (Not Technical Executability)

Gate-0 evidence:

- The frozen ZoomSpec pipeline **runs on CPU with existing dependencies and
  assets** through the scientific modules (`preprocessing.py`, `detector.py`,
  `frn.py` are device-parameterized; autocast is disabled off-CUDA).
- One CPU smoke on SpaceNet `advanced/test/0` completed in **~42.3 s** for the
  full run (plus ~3.9 s model construction) and produced **14 detections versus
  13 from the GPU oracle** in the Gate-0 comparison.
- Therefore ZoomSpec CPU is classified **technically feasible, not
  production-certified**.

Rules:

1. `technically_feasible` and `certified` are distinct fields.
2. Only `certified` capabilities are offered for run creation and exposed as
   runnable in the frontend.
3. ZoomSpec `local_cpu` remains `certified=False`; `cpu_supported` continues to
   read `False` in the compatibility projection.
4. A capability becomes certified only after an explicit gate records:
   accuracy within an agreed tolerance versus the reference device, an agreed
   latency envelope, and a reproducible evidence reference — captured in an
   `ExecutionCertificate`.
5. The two-environment boundary is preserved: the repo `.venv` control plane
   has no torch; the ML runtime is separate (`/root/miniconda3/bin/python` is
   the formal inference runtime). CPU certification never imports ML libraries
   into the control plane.

---

## 19. Auto Execution Policy (Deferred)

M9.2 does **not** auto-select the best executor for a plugin/run.

- Execution selection remains explicit and capability-driven
  (`executorPolicy.ts` semantics preserved).
- The platform may display declared and certified capabilities and availability,
  but must not silently choose an executor until capabilities and certificates
  are trustworthy and stable across plugins.
- Any automatic policy is deferred to a later milestone after at least two real
  plugins and one certified non-remote capability exist.

---

## 20. Acceptance Criteria

### 20.1 Framework

- `PluginRegistry` resolves a plugin by `(plugin_id, plugin_version)` with no
  torch/ultralytics imported in the control plane.
- A new plugin is added by declaring it (module path/config + manifest) with
  **zero** edits to control-plane logic.
- Parameters are validated against the declared schema before freeze; frozen
  plugins require `{}`.
- `model_release_id` is frozen, transmitted, verified, and persisted for new
  runs; same PluginVersion with different weights yields a distinct run
  provenance.
- The remote `ItemExecutor` is resolved through the registry; no ZoomSpec
  literal dispatch remains.
- `RuntimeDescriptor` replaces all `cuda:0` / device-0 hardcodes in probe,
  executor, package publisher, and result ingestor.
- The control plane and runner imports remain GPU-library-free.

### 20.2 Compatibility / regression

- All existing ZoomSpec scientific tests pass unchanged.
- The full M9.1 remote test suite passes (availability, create-run, coordinator,
  runner wiring, result ingest, public metadata).
- Analysis Package v1 schema and Algorithm Lab read paths are unchanged.
- A previously completed ZoomSpec run is still readable and comparable.

### 20.3 Second pipeline gate

- CPN-only/YOLO baseline is integrated as a plugin and runs through the same
  executor/ingest path, with no new platform persistence path.

---

## 21. M9.2A–F Decomposition

| Subtask | Scope | Depends on |
|---|---|---|
| M9.2A | Plugin identity + PluginVersion + `PluginDefinition` extensions + declarative `PluginRegistry` + parameter-schema validation. Additive; no behavior change. | — |
| M9.2B | `ModelRelease` + generic `AssetManifest` + version/release-scoped resolution and verification locally and remotely; thread `model_release_id` through provenance and wire additively. | A |
| M9.2C | `DatasetAdapter` boundary + generic remote recording resolver + parameter freeze replacing the hardcoded empty parameters. | A, B |
| M9.2D | `ExecutorRegistry` + `RuntimeDescriptor` + device resolution replacing `cuda:0`/device-0; generalized availability probe; CPU certification model. | A, B, C |
| M9.2E | ZoomSpec golden migration into the framework; seed golden ModelRelease; preserve all scientific/golden tests; record CPU status as feasible-not-certified. | A–D |
| M9.2F | Second real pipeline gate (CPN-only/YOLO baseline) + end-to-end acceptance; final self-review of provenance invariants. | E |

Dependency order: **A → B → C → D → E → F.** Each subtask is independently
testable; no subtask alone may claim multi-model genericity.

---

## 22. Risks

| Risk | Mitigation |
|---|---|
| Generalizing provenance weakens identity checks | Keep all M9.1 checks; add Release checks additively; regression tests |
| Plugin discovery reintroduces control-plane edits | Declarative module-path config / entry points; test that a dummy plugin needs no core edit |
| Asset path genericity creates path-injection risk | Validate env/config paths as absolute safe POSIX; wire carries logical names only |
| Device descriptor spoofing in results | Ingest validates against the run's persisted descriptor, not literals |
| Refactoring remote dispatch breaks M9.1 | Mandatory full remote regression suite; incremental migration steps |
| CPU work leaks torch into control plane | Import-boundary tests; ML runtime kept separate |
| CPN-only gate expands scope | Reuse existing detector/checkpoint; localization-only; no new training |
| Premature auto-policy | Explicitly deferred (§19); certification required first |
| ModelRelease vs PluginVersion confusion | Explicit rules in §5.2/§5.3 and compatibility refinement in §15 |

---

## 23. Out of Scope (M9.3)

- Batch/dataset evaluation across multiple pipelines or model releases.
- Benchmark protocol/membership changes for plugin comparison.
- Multi-model run scheduling, fan-out, or queueing.
- Auto execution policy.
- Any training, tuning, or model-selection platform.
- Any generic GPU-vendor or multi-GPU abstraction.

---

## 24. Summary of Invariants

1. Platform core branches on capabilities, never on concrete plugin ids.
2. Plugin declarations are torch-free; heavy ML imports are lazy and runtime-only.
3. `(plugin_id, plugin_version)` is immutable behavior; `model_release_id` is
   immutable assets under that behavior.
4. `asset_manifest_sha256` is the cryptographic asset identity, verified
   end-to-end.
5. The canonical request hash scheme, runtime-commit check, atomic ingest,
   terminal immutability, and physical-box/label validation are frozen.
6. Analysis Package v1 is unchanged; ModelRelease is internal execution
   provenance.
7. Exactly one result persistence path.
8. A capability is offered only when certified; technical executability alone
   never activates it.
9. Dataset access happens only through a DatasetAdapter.
10. Device selection happens only through a RuntimeDescriptor.

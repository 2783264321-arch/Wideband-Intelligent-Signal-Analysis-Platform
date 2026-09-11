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
3. Define a **frozen generic AssetManifest V1** with plugin-declared logical
   asset names, referenced (not modified) by an immutable ModelRelease.
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
- a `PluginDefinition` (metadata + declarations, torch-free);
- a parameter schema;
- technical execution capabilities (no certification claim);
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
trained weights / config / normalization. A new checkpoint, retrain, fine-tune, normalization, or config change qualifies
as a new ModelRelease **only when architecture, algorithm behavior contract,
parameter contract, and output semantics remain unchanged**. If a config change
alters network structure, behavior, parameters, or output semantics, it requires
a new **PluginVersion** instead. A ModelRelease **references** the existing
immutable AssetManifest V1 by its `asset_manifest_sha256`; it does not alter the
manifest or its hash payload.

### 5.4 AssetManifest V1 (Frozen Shape)

The AssetManifest V1 shape and canonical hash payload are **frozen**. It is a
plugin-declared mapping from **logical asset names** to SHA256 identities
(logical names defined by the plugin, for example `detector_checkpoint`,
`frn_checkpoint`, `ls_stft_normalization`, `frozen_config`). The manifest
self-hash excludes itself. `model_release_id` is **not** part of the manifest
payload; a ModelRelease is a separate record that binds a
`model_release_id` to an existing `asset_manifest_sha256`.

### 5.5 DatasetAdapter

The only component allowed to read a dataset-specific on-disk layout and map it
to a platform `RecordingInput`. A DatasetAdapter verifies the frozen recording
identity using the **recording's dataset label space** (the label semantics that
the dataset itself defines).

A Plugin runtime MAY read the IQ bytes at the already-resolved
`RecordingInput.data_path` for inference. It MUST NOT itself resolve dataset
layout, search for dataset files, inspect GroundTruth, or construct
dataset-specific paths.

### 5.6 ExecutionCapability (Technical Claim)

A plugin's **technical** execution claim: `local_cpu`, `local_gpu`, or
`remote_gpu`, with a device type and precision. A technical claim is not a
runnable grant; runnable status is derived by the platform from a platform-owned
`ExecutionCertificate` (§5.9, §18). A plugin MUST NOT self-declare
`certified`/runnable.

### 5.7 Input Compatibility vs Output Label Space

Two distinct concepts MUST NOT be conflated:

- **input compatibility** — which recording/dataset label spaces and dataset
  adapters a plugin accepts (for example a SpaceNet `spacenet_14` recording);
- **output label space** — the label semantics of the plugin's final
  `DetectionResult` classes (for example `spacenet_14` or
  `cpn_bandwidth_tier_v1`).

A plugin may accept `spacenet_14` recordings and emit a different output label
space. DetectionResult validation always uses the **output label space**.

### 5.8 RuntimeDescriptor

A concrete execution instance description (`executor`, `device_type`,
`device_index`, `precision`, `environment_ref`) that replaces device literals
such as `cuda:0` and `_DEVICE_INDEX = 0`. Only a defined public projection of a
RuntimeDescriptor enters the Analysis Package; the full descriptor stays in
internal provenance (§11.3).

### 5.9 ExecutionCertificate (Platform-Owned)

A platform-owned evidence record, precisely bound to `(plugin_id,
plugin_version, model_release_id, executor, device_type, precision, runtime
reference)`, that a combination has passed an acceptance gate. Certificates are
created and owned by the platform, never by a plugin declaration. A combination
without a certificate is not offered as runnable.

For a **release-less/code-only** plugin (`model_release_required=False`) the
resolved `model_release_id` is `None` and the certificate binds
`model_release_id=None`; certification is still mandatory. Plugins that require
immutable external assets must use a real ModelRelease (never a fake manifest).

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
│ Local inference worker         │   │ Remote plugin runtime             │
│  (separate process + ML env)   │   │  runner work: PluginItemExecutor  │
│  PluginExecutor registry       │   │  PluginRegistry + ExecutorRegistry │
│  lazy ML imports               │   │  DatasetAdapter -> RecordingInput  │
│  DatasetAdapter -> RecordingIn │   │                                    │
└───────────────────────────────┘   └───────────────────────────────────┘
```

### 6.2 Request/data flow (remote, generic)

```text
create_run(recording, plugin_id, plugin_version[, model_release_id], executor, params)
  -> PluginRegistry.get(plugin_id, plugin_version)            # torch-free, lazy
  -> validate params against plugin parameter schema         # torch-free
  -> resolve ModelRelease (explicit or default) + AssetManifest V1  # torch-free
  -> derive runnable executor from platform certificates      # torch-free
  -> check input compatibility vs recording dataset label space
  -> select ExecutorProvider for `executor`
  -> RuntimeDescriptor built by provider
  -> freeze provenance (request_sha256, model_release_id, asset_manifest_sha256)
  -> launch coordinator (remote) or inference worker (local)
       remote: runner resolves PluginItemExecutor for plugin_id
               -> DatasetAdapter resolves RecordingInput
               -> AssetManifest V1 resolution by (plugin_id, version, manifest_sha)
               -> PipelineExecution(... RuntimeDescriptor ...)
               -> PipelineOutput (Detections in output_label_space)
               -> Analysis Package v1 zip + envelope (write-once)
       local:  same scientific path in a separate inference worker (no SSH)
  -> ingest -> AnalysisResultWriter (output_label_space) -> SAME AnalysisRun
```

### 6.3 Invariant

The platform core never branches on a concrete plugin id. The only mappings from
`plugin_id` to behavior are plugin declarations loaded through a registry seam.

---

## 7. Ownership Boundaries

| Concern | Owner | Must not do |
|---|---|---|
| Plugin identity, definition, parameters, assets, technical capabilities | Plugin author | import torch at control-plane import time; self-declare certification; edit control-plane code |
| Scientific inference | Plugin runtime (separate inference worker) | read DB/SSH or resolve dataset layout/GroundTruth; run inside the API process (may read the resolved `RecordingInput.data_path` IQ) |
| Plugin discovery + validation | Platform `PluginRegistry` | embed plugin-specific constants |
| ModelRelease + AssetManifest V1 resolution | Platform `ModelReleaseStore` | hardcode asset names or paths; change the V1 hash payload |
| Certification / runnable derivation | Platform `ExecutionCertificate` store | accept plugin-declared certification |
| Execution selection + RuntimeDescriptor | Platform `ExecutorRegistry` | hardcode `cuda:0` / device 0 |
| Local inference worker runtime | Platform deployment config | live in the API process / control-plane `.venv` |
| Dataset resolution + input compatibility | `DatasetAdapter` + platform compatibility check | expose GroundTruth to inference; conflate input and output label spaces |
| AnalysisRun lifecycle / provenance / ingest | Platform control plane | change frozen M9.1 semantics |
| Remote transport | Platform `SshRunner` | accept request-controlled paths/secrets |
| Read model + UI | Platform frontend | hardcode plugin ids or device strings |

---

## 8. Plugin Contract

### 8.1 Identity

```text
plugin_id:      ^[a-z0-9][a-z0-9_]{0,127}$     (stable, global within platform)
plugin_version: opaque immutable identifier   (recommended: SemVer x.y.z)
plugin_api_version: integer (M9.2 = 1)
```

`plugin_version` is treated as an **immutable opaque version identifier**.
SemVer is **recommended** for new plugins but is **not mandatory**, so existing
historical identities such as `1.0` remain valid and unchanged. The platform
MUST NOT parse or reorder version semantics.

`plugin_id` replaces the current `PipelineDefinition.id` as the primary identity.
The existing read-model field `id` remains an alias for `plugin_id` so the
frontend and API contracts do not break.

### 8.2 PluginDefinition (extended)

The runtime `PipelineDefinition` (backend) and `PipelineDefinitionRead`
(API/frontend) are extended additively:

```text
plugin_id            (= existing `id`)
plugin_version       (= existing `version`; opaque)
plugin_api_version   (new)
name
label_space          (= output label space; retained for compatibility)
output_label_space   (new; authoritative output label space for DetectionResult validation)
input_compatibility  (new; accepted recording/dataset label spaces + required adapters)
task_capability      (classification | detection_localization | detection_classification)
stages
inspectable_stages
parameter_schema     (new; JSON Schema object; {} means no parameters)
technical_execution_capabilities   (new; ordered tuple of ExecutionCapability)
recommended_execution              (new; a technically supported executor)
model_release_required             (new; bool)
dataset_adapters                   (new; tuple of adapter ids)
```

`label_space` and `output_label_space` are the same value; `label_space` is
retained as the compatibility projection used by existing API/frontend and the
result writer. `input_compatibility` is evaluated against the **recording's
dataset label space**, independently of `output_label_space` (§10).

`executors_supported` and `recommended_executor` are the
**deployment-qualified executor projection** (configuration + certification, not
live readiness):

- `executors_supported` = a plugin's declared `technical_execution_capabilities`
  ∩ an executor for which this deployment has a **registered provider** ∩ an
  **exact `ExecutionCertificate`** for the default resolved release
  (`model_release_id=None` for release-less plugins). It is computed from
  configuration and certification only and MUST NOT trigger SSH/probe I/O.
- `recommended_executor` is `definition.recommended_execution` **only when** that
  executor is in `executors_supported`; otherwise it is `None`. The platform
  never substitutes a different executor.
- `executors_supported` does **not** imply live probe readiness. Whether an
  executor can run *right now* for a specific recording remains the
  responsibility of executor availability / `create_run` (§9.5, §11.2).
- `technical_execution_capabilities` remains the declared technical truth.
  `cpu_supported` is legacy technical metadata only and MUST NOT imply that
  `local_cpu` is runnable.

`PipelineDefinitionRead.recommended_executor` is therefore `str | None` (frontend
`recommendedExecutor: string | null`).

### 8.3 Parameter schema

Each plugin declares a JSON Schema object for `parameters`. M9.2 supports the
subset: `type`, `properties`, `required`, `additionalProperties=false`,
`enum`, numeric `minimum`/`maximum`, and `default`. The control plane validates
parameters before run creation **without importing plugin scientific code**.
Frozen plugins declare an empty object schema. This replaces the hardcoded
`"parameters": {}` in `backend/app/remote_execution/request_builder.py:140`.

### 8.4 Technical execution capabilities (plugin-declared, no certification)

A plugin declares only its **technical** execution claims:

```text
technical_execution_capabilities = (
  ExecutionCapability(executor="remote_gpu", device_type="cuda", precision="float16"),
  ExecutionCapability(executor="local_cpu",  device_type="cpu",  precision="float32"),
)
recommended_execution = "remote_gpu"
```

Rules:

- A plugin MUST NOT declare `certified`, `runnable`, or a certificate reference.
- The platform computes the runnable set as
  `technical_execution_capabilities ∩ certified certificates` for the exact
  `(plugin_id, plugin_version, model_release_id, executor, device_type,
  precision)` tuple.
- Because certification is release-specific, the same PluginVersion may expose
  different runnable executors for different ModelReleases.
- `recommended_execution` is only meaningful after certification; the read model
  MUST NOT present an uncertified executor as runnable.

### 8.4.1 Certification is platform-owned

An `ExecutionCertificate` is created only by the platform's acceptance process
(§18, §21.1). It binds `(plugin_id, plugin_version, model_release_id, executor,
device_type, precision, runtime reference)`. A plugin cannot create, widen, or
override a certificate.

### 8.5 Plugin registry seam

```text
PluginRegistry
  .get(plugin_id, plugin_version) -> PluginHandle (definition + lazy factory ref)
  .list() -> [PluginDefinition]
```

- The registry loads plugin **declarations** only. It MUST be importable with no
  torch/ultralytics (`test_zoomspec_definition_registry.py` boundary preserved).
- Discovery is declarative: a plugin registry configuration lists dotted module
  paths (built-in reference plugins are listed by default). Adding a plugin
  appends a dotted path to configuration, not logic to `registry.py`.
  Out-of-tree plugins MAY use an `importlib.metadata` entry-point group
  (`wisa.pipeline_plugins`); this is optional packaging, not required for M9.2.
- A declaration module exposes metadata and **lazy references only** (dotted
  factory paths / callables resolved at execution time). It MUST NOT import
  scientific or heavy modules (`torch`, `ultralytics`, detector/FRN/STFT code)
  at module import. The scientific `Pipeline` is constructed only by a plugin
  runtime inside a workspace (local inference worker or remote executor), never
  by the control plane.

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
asset_manifest_sha256   (reference to an existing AssetManifest V1)
created_at (informational)
notes (informational, e.g. training run reference)
```

Changing weights/config/normalization creates a **new ModelRelease** that
references a **new AssetManifest V1** (new `asset_manifest_sha256`), not a new
PluginVersion. Changing code or architecture creates a **new PluginVersion**.

### 9.2 AssetManifest V1 (hash payload frozen)

AssetManifest V1 is unchanged from M9.1. Its canonical payload and self-hash are
frozen; `model_release_id` MUST NOT be added to it.

```json
{
  "pipeline_id": "zoomspec_yolo26n_aug_combined_frn_v3",
  "pipeline_version": "1.0.0",
  "assets": {
    "detector_checkpoint": "<sha256>",
    "frn_checkpoint": "<sha256>",
    "frozen_config": "<sha256>",
    "ls_stft_normalization": "<sha256>"
  },
  "asset_manifest_sha256": "<self-excluding canonical hash>"
}
```

The existing ZoomSpec `asset_manifest_sha256`
(`16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08`) MUST
remain byte-identical for the unchanged golden assets.

A ModelRelease is a **separate record** that binds a `model_release_id` to a
`plugin_id`/`plugin_version` and an `asset_manifest_sha256`. The two identities
are frozen and verified independently:

- `model_release_id` — logical release identity (frozen in run provenance);
- `asset_manifest_sha256` — cryptographic asset identity (verified against the
  actual AssetManifest V1 and asset bytes).

The legacy manifest field names `pipeline_id`/`pipeline_version` remain the
wire-level keys for compatibility; `plugin_id`/`plugin_version` are aliases over
the same values.

### 9.3 Resolution and verification

- Local control plane resolves `(plugin_id, plugin_version, model_release_id)`
  -> ModelRelease record -> referenced `asset_manifest_sha256`. The referenced
  hash is frozen into run provenance.
- Remote runtime resolves the ModelRelease and its AssetManifest V1 by
  `(plugin_id, plugin_version, asset_manifest_sha256)` and verifies:
  - the referenced AssetManifest V1 exists for the plugin/version;
  - its self-hash equals the request's `asset_manifest_sha256` (V1 payload is
    unchanged, so the hash is computed exactly as in M9.1);
  - the ModelRelease record's `asset_manifest_sha256` equals the manifest
    self-hash, and its `model_release_id` equals the request's
    `model_release_id`;
  - each asset file byte-hash matches the manifest;
  - the deployed runtime commit matches the required commit.
- Asset file paths come **exclusively** from a release/manifest-namespaced
  deployment mapping keyed at least by `(plugin_id, plugin_version,
  asset_manifest_sha256, logical_asset_name)` (validated absolute safe POSIX
  paths). They are never taken from the wire request, the plugin declaration,
  the ModelRelease record, or the manifest. The manifest stores logical names +
  SHA256 only. See §11.4.

### 9.4 Same-architecture retraining rule

```text
new retrained checkpoint
  -> new AssetManifest V1 (new asset_manifest_sha256)
  -> new ModelRelease referencing that hash
  -> new AnalysisRun records new model_release_id + asset_manifest_sha256
  -> PluginVersion unchanged; platform code unchanged
```

This requires release/version-scoped AssetManifest V1 resolution (§9.3) and
removal of the hardcoded manifest path currently in
`backend/app/main.py:30-35` and
`backend/app/remote_execution/worker_context.py:110-114`.

### 9.5 Resolved ModelRelease selection (V1)

Certification, executor availability, compatibility projections, and
`recommended_executor` are all **release-specific**, so a run must resolve a
concrete ModelRelease before any of them can be evaluated. For a
release-less/code-only plugin (`model_release_required=False`) the resolved
release is `None` and projections/certification use `model_release_id=None`.

V1 selection rule:

```text
deployment/platform-owned:
  default_model_release_id[plugin_id, plugin_version]

create_run(recording, plugin_id, plugin_version[, model_release_id], executor, params)
  requested_release = explicit model_release_id, if provided
  resolved_release  = requested_release or default_model_release_id
  resolved_manifest = ModelRelease(resolved_release).asset_manifest_sha256
```

Rules:

- The platform owns the per-`(plugin_id, plugin_version)`
  `default_model_release_id`. A normal UI run uses it.
- API/advanced callers MAY explicitly request a release; the requested release
  must exist for that plugin version, else `MODEL_RELEASE_NOT_FOUND`.
- **All** of executor availability, `certified_capabilities`,
  `executors_supported` / `recommended_executor` projections, input/output
  compatibility, and certificate lookup use the **resolved** ModelRelease
  (`model_release_id=None` for release-less plugins).
- Retraining + deployment of new assets/ModelRelease + switching the default is
  a configuration change; no platform code change is required (§9.4).
- M9.2 requires **no frontend release-management UI**; the default is sufficient
  for the UI, and explicit selection is an API-level capability.
- The resolved `model_release_id` and its `asset_manifest_sha256` are frozen
  into run provenance.

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
- The fingerprint/identity check uses the **recording's dataset label space**
  (the label semantics defined by the dataset), independent of the plugin's
  output label space.
- GroundTruth is inspected only for fingerprint verification and MUST NOT reach
  inference.
- Adapters are selected by `dataset_name`; `resolve_space_net` becomes the
  built-in `SpaceNetAdapter` implementation of this interface.
- The local path already satisfies the boundary: the Recording model is mapped
  to `RecordingInput` in `backend/app/analysis/worker.py`.

### 10.3 Plugin binding and input compatibility

A plugin declares two independent things:

```text
input_compatibility   (accepted recording/dataset label spaces + required adapters)
output_label_space    (label semantics of the emitted DetectionResult classes)
```

The executor resolves the adapter by `recording.dataset_name` and checks the
recording's dataset label space against `input_compatibility`; a plugin that
does not accept the dataset fails closed before model construction.
`output_label_space` is used only for DetectionResult/label validation and never
to gate input compatibility.

Example (CPN-only, §17):

```text
input_compatibility = ("spacenet_14",)   # SpaceNet recording identity
output_label_space  = "cpn_bandwidth_tier_v1"
```

A plugin may accept `spacenet_14` recordings and emit a different output label
space. DetectionResult validation always uses `output_label_space`.

---

## 11. Executor / Runtime Abstraction

### 11.1 Executor kinds

| Executor | Where it runs | Heavy deps | Selection |
|---|---|---|---|
| `local_cpu` | A **separate configured inference worker process** using its own ML interpreter; never the API process | plugin runtime deps (may include torch on the ML interpreter) | platform certificate required |
| `local_gpu` | Same separate inference worker model on a GPU host | torch/CUDA on the ML interpreter | platform certificate required |
| `remote_gpu` | Detached remote worker over SSH | torch/CUDA/ultralytics | asset + runtime probe required |

`local_cpu` and `local_gpu` are **out-of-process inference workers**, not
in-process calls. The API process and control plane (repo `.venv`) never import
torch, even for `local_cpu`; the worker is configured with its own interpreter
and dependencies. `local_gpu` is introduced by the abstraction but is **not
required to be implemented** in M9.2; it exists so the framework does not assume
all GPU work is remote.

### 11.2 ExecutorRegistry

```text
ExecutorRegistry
  .provider(executor_name) -> ExecutorProvider
  .technical_capabilities(plugin_definition) -> [ExecutionCapability]
  .certified_capabilities(plugin_id, plugin_version, model_release_id) -> [ExecutionCapability]
  .availability(plugin_definition, model_release, runtime_descriptor, recording) -> Availability
```

`AnalysisService` dispatches by capability, not by the literal string
`remote_gpu` (`backend/app/analysis/service.py:147,191`). `create_run` validates
and launches the **exact requested executor**; it never auto-substitutes another
certified executor. The existing `RemoteExecutorProbe` protocol is one
`ExecutorProvider` implementation. A capability is runnable only when it is both
technically claimed by the plugin and covered by a platform
`ExecutionCertificate` for the exact release/executor.

`model_release_id` in `certified_capabilities` and `model_release` in
`availability` are the **resolved** ModelRelease (§9.5); release-less plugins use
`model_release_id=None`. Callers that have not selected a release use the platform
default for the plugin version, so executor projections and availability are
always computed against a concrete release (or `None` for release-less plugins).

`ExecutorRegistry` also exposes an **exact-executor availability seam** (for
example an `availability(plugin_definition, model_release, recording, executor)`
method) that checks the requested executor's technical capability, exact
certificate, provider `runtime_ref`, and provider probe. `/api/executor-availability`
may accept an optional `executor` query parameter with a backward-compatible
default of `remote_gpu`. The deployment-qualified `executors_supported` projection
(§8.2) is **not** live readiness; only this seam answers recording/runtime-specific
availability.

### 11.3 RuntimeDescriptor and public package projection

```text
RuntimeDescriptor (internal)
  executor:       local_cpu | local_gpu | remote_gpu
  device_type:    cpu | cuda
  device_index:   int | None
  precision:      float32 | float16
  environment_ref: str | None
```

- Built by the selected `ExecutorProvider` (from `RemoteProfile` for remote,
  from local inference-worker deployment config for local).
- Passed to plugin pipeline construction instead of integer `0`.
- Persisted in full in `execution_metadata_json` (internal provenance).
- `backend/app/remote_execution/result_ingestor.py:150` MUST validate the
  package execution metadata against the run's persisted `RuntimeDescriptor`,
  not the literal `cuda:0`.

**Public Analysis Package projection (frozen v1 boundary, unchanged).** Only a
minimal, non-sensitive projection enters the public package
`ExecutionMetadata`:

```text
executor    = executor
device      = "cpu" | "cuda:N"        (N = device_index when device_type = cuda)
environment = non-sensitive logical label, or None
```

`precision`, `environment_ref`, interpreter/runtime paths, profile references,
and any deployment/asset paths remain internal and MUST NOT appear in the public
package, the public API, or the envelope's public projection. Paths, secrets, and
credentials are never exposed.

### 11.4 Remote asset + runtime context and trusted path mapping

`RemoteWorkerContext` is generalized **additively** by D4; the production
`runner._cli_work` cutover is D3B, so the legacy path must remain intact during the
transition:

- it gains `repo_root`, `job_root`, `required_runtime_commit`, a `manifest_root`,
  and an **asset path configuration** validated as absolute safe POSIX paths,
  namespaced by `(plugin_id, plugin_version, asset_manifest_sha256)`;
- generic plugin-declared logical asset names are resolved through that namespaced
  configuration. Generic resolution MUST NOT read the legacy ZoomSpec-specific
  fields (`detector_checkpoint`, `frn_checkpoint`, `frozen_config`,
  `ls_stft_normalization`) as plugin knowledge;
- the **legacy ZoomSpec scalar env vars/fields are preserved during the D4
  transition**, so a legacy-only worker configuration that still drives the
  current `ZoomSpecRemoteItemExecutor` (`runner._cli_work`) keeps constructing and
  executing until D3B. A single deployment config object MAY carry **both** the
  legacy flat subset (`logical -> "/abs"`) and the generic namespaced subset
  (`"<plugin_id>/<plugin_version>/<sha256>" -> {logical: "/abs"}`); D4 partitions
  them deterministically and rejects malformed/ambiguous entries. `SshRunner`
  derives the legacy scalar envs from the flat subset and sends only the generic
  namespaced subset as `WSP_REMOTE_ASSET_PATHS_JSON` (compact, shell-quoted so it
  survives the remote login shell). Legacy fields are retired only by the D3B
  cutover / an explicit post-D3B cleanup — generic-only operation is **not**
  required before D3B;
- generic trusted-asset/runtime APIs MUST fail closed (`REMOTE_WORKER_CONTEXT_INVALID`)
  when their own generic configuration is absent or invalid; they MUST NOT
  silently fall back to legacy ZoomSpec fields;
- D4 MUST NOT modify `runner._cli_work` or switch production dispatch (D3B owns
  that); separate generic readiness/config-completeness helpers may be added
  without changing the legacy required-env semantics prematurely;
- it still carries **no** SSH/credential reference and no request-controlled
  path;
- parsing still fails closed on any missing/unsafe field.

**Trusted path mapping (release/manifest-namespaced).** There is no global
logical-name -> path map. Deployment configuration provides a namespaced mapping
keyed at least by:

```text
(plugin_id, plugin_version, asset_manifest_sha256, logical_asset_name) -> absolute path
```

or an equivalent manifest-scoped structure. This prevents collisions when
different plugins (or different ModelReleases of one PluginVersion) reuse logical
asset names such as `detector_checkpoint`. The mapping comes exclusively from
validated deployment configuration (operator-owned env/config on the executor
host). It is never derived from the wire request, the plugin declaration, the
ModelRelease record, or the AssetManifest V1. The platform MUST NOT dynamically
trust a path value received over the wire. The manifest carries logical names +
SHA256 only.

**Readiness probe.** Executor readiness is probed per `RuntimeDescriptor`; the
platform MUST NOT assume any device is always ready. A `local_cpu`/`local_gpu`
probe verifies the configured inference interpreter, the plugin's runtime
dependencies, and the resolved assets. `remote_gpu` is **CUDA-only** in M9.2: a
non-`cuda` remote descriptor fails closed (there is no remote-CPU readiness
path). A `remote_gpu` probe verifies the pinned runtime commit, asset manifest +
bytes, dataset root, and a configured **non-null, non-negative** CUDA
`device_index` (never substituted with `0`). The existing
`backend/app/remote_execution/probe.py` device-0 assumption is replaced by
descriptor-driven checks, and `RemoteProfile.runtime_descriptor()` and the
worker's reconstructed descriptor agree after the transport env bridge, including
environment identity.

### 11.5 ItemExecutor / Plugin registry seam

`backend/app/remote_execution/runner.py` `_cli_work` currently hardcodes
`ZoomSpecRemoteItemExecutor`. M9.2 replaces remote item dispatch with a generic
`PluginItemExecutor`. The cutover is split so the M9.1 ZoomSpec remote production
path stays intact until the generic asset/runtime/package seams exist.

**Sequencing (approved ruling): D3A → D4 → D5 → D3B.** The original single D3 was
BLOCKED with no code change: the generic executor's trusted-assets and package
seams depend on D4/D5, and cutting `runner._cli_work` before D4 would require
ZoomSpec hardcoding. Until D3B, the ZoomSpec remote path remains the active path.

**D3A — generic executor core (no runner change).** A generic
`PluginItemExecutor` (`backend/app/remote_execution/plugin_executor.py`) owns
request verification/orchestration only and depends on **injected generic seams**
(trusted-assets resolver, `RuntimeDescriptor`, package-publishing callable):

```text
PluginItemExecutor (generic; injected seams; no ZoomSpec constants)
  execute(item, job_root):
    verify batch.required_remote_runtime_commit == worker.required_runtime_commit
    verify canonical request_sha256
    handle   = plugin_registry.get(batch.pipeline.id, batch.pipeline.version)
               (verify definition id/version; batch.pipeline.model_release_id)
    release  = model_release_store.resolve(exact wire model_release_id)
               (verify release.manifest.asset_manifest_sha256 == batch.asset_manifest_sha256)
    assets   = trusted_assets_resolver(plugin_id, plugin_version, asset_manifest_sha256, manifest)
    resolved = adapter_registry.get(item.recording.dataset_name).resolve(...)
    validate_plugin_parameters(handle.definition, item.parameters)
    label_space = LabelSpaceService(worker.label_space_root).get(
        handle.definition.resolved_output_label_space)
    runtime  = handle.load_runtime(assets=assets, runtime_descriptor=runtime_descriptor,
                                   output_label_space=label_space)
    output   = runtime.execute(resolved.recording_input, item.parameters, workspace)
    package_publisher(output, handle.definition, runtime_descriptor, item, job_root, ...)
```

- **D4** supplies the production `RemoteWorkerContext` (namespaced trusted asset
  mapping), the deployment-owned `RuntimeDescriptor`, and the descriptor-driven
  probe.
- **D5** supplies the production package publisher/ingestor using
  `handle.definition` + output label space + `RuntimeDescriptor.public_projection()`.
- **D3B** performs the final `runner._cli_work` cutover to `PluginItemExecutor`
  using the D4/D5 production dependencies and removes the ZoomSpec literal dispatch.
- D3A and D4/D5 MUST NOT pre-implement each other; D3A and D4/D5 MUST NOT switch
  the runner (only D3B does).

**D3B cutover facts (implemented).** The production runner now constructs the
deployment-owned generic dependencies (`PluginRegistry`, `ModelReleaseStore`,
`DatasetAdapterRegistry`, `ExecutionCertificateStore`) plus
`worker.resolve_assets`, `worker.runtime_descriptor()` and the D5 package
publisher, and dispatches every free item through `PluginItemExecutor`; no
plugin-id/ZoomSpec constants remain in `runner.py`. `ZoomSpecRemoteItemExecutor`
is retained but is **legacy / no longer on the production dispatch path** (its
plugin runtime factory is supplied by E1). `PluginItemExecutor` enforces an
item-must-belong-to-the-frozen-batch binding at the generic boundary. The legacy
worker-context scalar env fields (`required_worker_env_vars` + the four ZoomSpec
asset scalars) are retained as **post-cutover cleanup debt** and are not read by
generic execution. Generic-only remote **production deployment remains blocked**
by the legacy transport preflight (`SshRunner.validate_runner_environment`
still requires the four flat assets): `D3B_BLOCKED_BY_LEGACY_TRANSPORT_PREFLIGHT`;
generic-only operation awaits the post-D3B legacy retirement. Release-less remote
execution stays fail-closed (unchanged).

The generic executor owns verification/orchestration; the plugin owns science.
ZoomSpec becomes one registered plugin, not a special case. GroundTruth never
reaches inference; request-controlled filesystem paths are never accepted. Remote
release-less execution stays fail-closed (the frozen wire requires
`asset_manifest_sha256`) unless a later approved task generalizes the wire.

---

## 12. Control-plane / Runtime Separation

- The control plane (`backend/app/analysis`, `backend/app/pipelines` registry
  declarations, `backend/app/remote_execution` except the lazy `work`/`probe`
  bodies, `backend/app/main.py`) MUST remain torch/ultralytics-free.
- `local_cpu` and `local_gpu` are **separate inference worker processes** with
  their own configured ML interpreter. The API process and the repo `.venv`
  control plane MUST NEVER import torch/ultralytics, including for `local_cpu`.
  "CPU" refers to the worker runtime, not the API process.
- Plugin declaration modules MUST import without torch. Heavy imports are lazy
  inside execution methods (the pattern already used by
  `zoomspec.../detector.py`, `frn.py`, `preprocessing.py`).
- Plugin declaration modules MUST expose only metadata + lazy factory
  references; they MUST NOT import scientific/heavy modules at declaration
  import time.
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
| `EXECUTION_CAPABILITY_UNAVAILABLE` | executor technically unsupported by plugin |
| `EXECUTION_NOT_CERTIFIED` | executor lacks a platform certificate for the release |
| `RUNTIME_DESCRIPTOR_INVALID` | malformed/unsupported descriptor |
| `INPUT_INCOMPATIBLE` | recording dataset label space not accepted by plugin |
| `OUTPUT_LABEL_SPACE_INVALID` | output label space unknown/invalid for the plugin |
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
2. AssetManifest V1's canonical hash payload is unchanged. Its
   `asset_manifest_sha256` remains the cryptographic asset identity and is
   verified end-to-end (control plane, probe, remote executor, ingest). The
   unchanged ZoomSpec golden hash `16cc0534...` MUST remain valid.
3. `plugin_id` and `plugin_version` remain frozen into the request, envelope,
   package, and run.
4. `model_release_id` is a separate immutable identity that **references** an
   existing `asset_manifest_sha256`. It is frozen into the request metadata and
   the remote envelope, and is verified against the ModelRelease record's bound
   manifest hash; it is never added to the AssetManifest V1 payload.
5. A completed AnalysisRun is immutable. Ingest is idempotent for equal payload
   SHA and conflicts otherwise.
6. Runtime commit verification remains mandatory before inference.
7. Physical-box validation remains mandatory on ingest; label validation uses
   the plugin's `output_label_space` (which equals the frozen `label_space`
   projection).
8. `failed`/`interrupted`/`completed` runs are never overwritten.
9. Parameters are frozen into provenance after schema validation; no
   post-freeze mutation.
10. GroundTruth never reaches inference.
11. Trusted asset paths come only from validated deployment configuration, never
    from the wire, plugin declaration, ModelRelease, or AssetManifest.
12. The public Analysis Package projection exposes only `executor`, `device`
    (`cpu`/`cuda:N`), and a non-sensitive `environment` label; precision and
    private runtime/environment references stay internal.

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
   are retained as the deployment-qualified projection (§8.2).
   `recommended_executor` becomes nullable (`str | None`); `executors_supported`
   remains a list (empty when nothing is deployment-qualified).
2. `PluginDefinition.id` remains the API-visible identity; existing
   `AnalysisRun.pipeline_id` values are unchanged.
3. Existing completed ZoomSpec runs remain valid. Their `model_release_id` is
   derived from the already-persisted `asset_manifest_sha256` during M9.2E and
   recorded for new runs; historical rows are not rewritten.
4. Algorithm Integration Standard v1 §14 (checkpoint change => new Pipeline
   version) is refined by this spec: under the plugin framework, a checkpoint/
   config/normalization change is a new **ModelRelease** referencing a new
   AssetManifest V1, not necessarily a new PluginVersion. This is the single
   deliberate semantic refinement of the v1 standard and MUST be reflected in
   the standard's next revision note.
5. Legacy version identities are preserved. `plugin_version` is an opaque
   immutable identifier; existing `"1.0"` values (STFTEnergy, Dummy) remain
   valid. SemVer is recommended for new plugins only.
6. Legacy task capabilities are preserved. `task_capability` continues to
   accept existing values including `classification`; new capabilities are
   additive.
7. `label_space` remains a valid API field and equals `output_label_space`.
   Existing plugins keep their current output label space. Input compatibility
   is evaluated separately and must not change existing accepted recordings.
8. Analysis Package v1, benchmark manifests, imported-batch identities, and
   Algorithm Lab comparisons remain unchanged.

---

## 16. Migration Strategy (ZoomSpec golden plugin)

The default response to heterogeneity is: **write a Plugin, do not change the
control plane.**

| Step | Change | Rollback risk |
|---|---|---|
| 1 | Introduce `PluginDefinition`/registry types additively; existing ZoomSpec definition maps onto them | none (behavior unchanged) |
| 2 | Wrap ZoomSpec scientific modules behind the plugin declaration; keep numeric constants and orchestration identical | low; covered by existing ZoomSpec tests |
| 3 | Seed one ModelRelease record that references the current unchanged `asset_manifest.json` (`16cc0534...`); preserve all four logical names and SHAs | low |
| 4 | D3A: generic `PluginItemExecutor` core with injected asset/runtime/package seams (no runner change) | low; covered by new `test_plugin_executor.py` |
| 5 | D4: generic worker context/namespaced assets + descriptor-driven probe; D5: descriptor-driven package publisher/ingestor; D3B: final `runner._cli_work` cutover to `PluginItemExecutor` (removes ZoomSpec literal dispatch) | medium; covered by worker-context/probe/publisher/runner tests |
| 6 | Add the CPN-only second plugin (§17) | low |
| 7 | Record ZoomSpec CPU status as technically feasible / not certified (§18) | none |
| 8 | Run the consolidated final live GPU acceptance gate (§21.1) before pinning a new runtime | medium; CPU-only work through A–F otherwise |

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
- Proves the input/output label-space split: the CPN detector emits three
  bandwidth tiers (narrow/mid/wide), which are **not** SpaceNet classes.

Expression under the framework:

```text
plugin_id          = cpn_bandwidth_tier
input_compatibility = ("spacenet_14",)              # SpaceNet recording identity
dataset_adapters    = ("spacenet",)
output_label_space  = "cpn_bandwidth_tier_v1"       # class_id 0/1/2 -> narrow/mid/wide
task_capability     = "detection_classification"
executor            = "remote_gpu" (certificate required before runnable)
```

The plugin accepts SpaceNet `spacenet_14` recordings but validates its
`DetectionResult` classes against `cpn_bandwidth_tier_v1`, independent of the
recording's dataset label space. This is the concrete case that the input/output
split (§10.3) makes expressible.

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

1. A plugin declares `technical_execution_capabilities` only. `certified` is
   never a plugin-declared boolean.
2. Runnable status is **derived by the platform** from an
   `ExecutionCertificate` bound to the exact
   `(plugin_id, plugin_version, model_release_id, executor, device_type,
   precision, runtime reference)`. Certificates are platform-owned (§5.9).
3. Only certified combinations are offered for run creation and exposed as
   runnable in the frontend.
4. ZoomSpec `local_cpu` remains without a certificate; `cpu_supported` is legacy
   technical metadata only and MUST NOT imply `local_cpu` is runnable. The
   deployment-qualified `executors_supported` projection (§8.2) is the runnable set.
5. A certificate is created only after an explicit platform gate records:
   accuracy within an agreed tolerance versus the reference device, an agreed
   latency envelope, and a reproducible evidence reference. Because
   certification is release-specific, retraining invalidates prior CPU
   certificates for that plugin version until re-certified.
6. `local_cpu`/`local_gpu` execute in a separate configured inference worker
   process/runtime; the API process never runs inference and stays torch-free.
7. CPU readiness is probed, not assumed: the worker's interpreter, the plugin's
   runtime dependencies, and the resolved assets must all be available
   (see §11.4). The presence of a CPU does not by itself establish readiness.
8. The two-environment boundary is preserved: the repo `.venv` control plane
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
  torch/ultralytics imported in the control plane; declaration modules expose
  metadata + lazy factory references only.
- A new plugin is added by declaring it (module path/config + manifest) with
  **zero** edits to control-plane logic.
- Parameters are validated against the declared schema before freeze; frozen
  plugins require `{}`.
- `model_release_id` is frozen, transmitted, verified, and persisted for new
  runs; same PluginVersion with different weights yields a distinct run
  provenance, while the referenced AssetManifest V1 hash payload is unchanged.
- Runnable executors are derived from platform-owned certificates, never from a
  plugin-declared boolean.
- A `(plugin_id, plugin_version)` default ModelRelease resolves when the caller
  gives no explicit release; executor availability, capability projections, and
  certificate lookup use that resolved release (`model_release_id=None` for
  release-less plugins).
- The remote `ItemExecutor` is resolved through the registry; no ZoomSpec
  literal dispatch remains (finalized by D3B via the D3A/D4/D5 path).
- `RuntimeDescriptor` replaces all `cuda:0` / device-0 hardcodes in probe,
  executor, package publisher, and result ingestor; the public package exposes
  only `executor`, `device` (`cpu`/`cuda:N`), and non-sensitive `environment`.
- Input compatibility is evaluated against the recording's dataset label space;
  DetectionResult validation uses `output_label_space`.
- `local_cpu`/`local_gpu` run in a separate inference worker process; the API
  process and repo `.venv` remain torch-free.
- The control plane and runner imports remain GPU-library-free.

### 20.2 Compatibility / regression

- All existing ZoomSpec scientific tests pass unchanged.
- The full M9.1 remote test suite passes (availability, create-run, coordinator,
  runner wiring, result ingest, public metadata).
- Analysis Package v1 schema and Algorithm Lab read paths are unchanged.
- A previously completed ZoomSpec run is still readable and comparable.

### 20.3 Second pipeline gate

- CPN-only/YOLO baseline is integrated as a plugin with
  `input_compatibility=("spacenet_14",)` and
  `output_label_space="cpn_bandwidth_tier_v1"`, and runs through the same
  executor/ingest path, with no new platform persistence path.

### 20.4 Final live GPU acceptance

- The consolidated live gate in §21.1 passes before any new runtime pin.
- `5bb5be4` remains the historical accepted runtime baseline; it is not
  modified or reused as the M9.2 runtime.

---

## 21. M9.2A–F Decomposition

| Subtask | Scope | Depends on |
|---|---|---|
| M9.2A | Plugin identity + PluginVersion (opaque) + `PluginDefinition` extensions (input compatibility, output label space, parameter schema, technical capabilities) + declarative `PluginRegistry` with lazy factory references. Additive; no behavior change. | — |
| M9.2B | `ModelRelease` + frozen AssetManifest V1 + version/release-scoped resolution and verification locally and remotely; thread `model_release_id` through provenance and wire additively. | A |
| M9.2C | `DatasetAdapter` boundary + input-compatibility/output-label-space split + generic remote recording resolver + parameter freeze replacing the hardcoded empty parameters. | A, B |
| M9.2D | `ExecutorRegistry` + `RuntimeDescriptor` + platform-owned `ExecutionCertificate` store + device resolution replacing `cuda:0`/device-0; separate local inference worker runtime; descriptor-driven availability probe. | A, B, C |
| M9.2E | ZoomSpec golden migration into the framework; seed golden ModelRelease referencing the unchanged manifest; preserve all scientific/golden tests; record CPU as technically feasible / not certified. | A–D |
| M9.2F | Second real pipeline gate (CPN-only/YOLO baseline, `cpn_bandwidth_tier_v1` output). Development/tests are no-card; the plugin declares the required `remote_gpu` technical capability and is exercised through `remote_gpu` only in the consolidated final live GPU gate (§21.1). | E |

Dependency order: **A → B → C → D → E → F.** Each subtask is independently
testable; no subtask alone may claim multi-model genericity. Development and
tests for A–F are no-card; plugins still declare their real technical
capabilities (for CPN-only, `remote_gpu`), which are exercised on GPU only in the
consolidated final live gate (§21.1).

### 21.1 Final Live GPU Acceptance Gate (consolidated, after A–F)

M9.2 modifies runner dispatch, probe, worker context, remote result validation,
and asset/device resolution — all on the real remote runtime path. Passing
`pytest` alone does not declare the new runtime production-ready. Run one
consolidated live gate after the CPU-only implementation (A–F) is complete:

```text
1. real SSH probe (remote runtime reachable, assets/commit/dataset valid)
2. ZoomSpec golden remote inference (existing frozen semantics)
3. CPN-only remote inference through the generic plugin/executor seam
4. package <-> DB parity for both runs (detections, label spaces, provenance)
5. appropriate Algorithm Lab verification
```

Rules:

- The gate is the only place a new runtime pin is allowed to replace the
  historical accepted baseline `5bb5be4`; `5bb5be4` is retained as the
  historical accepted runtime.
- If the implementation does **not** touch coordinator/recovery/transport, the
  full M9.1 Gate B suite need not be rerun mechanically; if it does touch those,
  the acceptance scope is escalated accordingly.
- CPU-only work through A–F MUST NOT consume GPU; the live gate is a single
  consolidated event.
- The gate records an `ExecutionCertificate` (where applicable) and the runtime
  pin in the acceptance evidence.

---

## 22. Risks

| Risk | Mitigation |
|---|---|
| AssetManifest V1 hash changes and invalidates golden identity | ModelRelease references `asset_manifest_sha256`; V1 payload frozen; golden `16cc0534...` asserted |
| Plugin self-certifies a runnable path | Plugin declares technical capability only; platform-owned release-specific `ExecutionCertificate` |
| Control plane imports torch for `local_cpu` | Separate inference worker runtime; API process/.venv torch-free; import-boundary tests |
| Input and output label spaces conflated | Separate `input_compatibility` and `output_label_space`; CPN-only gate proves it |
| Device descriptor spoofing in results | Ingest validates against the run's persisted descriptor, not literals |
| Private runtime info or paths leak into the public package | Frozen projection (`executor`, `device`, non-sensitive `environment`); full descriptor internal |
| Trusted asset path injection | Release/manifest-namespaced mapping from validated deployment config only; wire carries logical names |
| Asset logical-name collision across plugins/releases | Namespace paths by `(plugin_id, plugin_version, asset_manifest_sha256, logical_asset_name)` |
| Executor projection evaluated before a release is known | Per-version platform default ModelRelease; projections use the resolved release (`None` for release-less) |
| Config change wrongly shipped as a ModelRelease | ModelRelease only when architecture/behavior/params/output semantics unchanged; else new PluginVersion |
| Generalizing provenance weakens identity checks | Keep all M9.1 checks; add Release checks additively; regression tests |
| Plugin discovery reintroduces control-plane edits | Declarative module-path config / entry points; test that a plugin needs no core edit |
| Refactoring remote dispatch breaks M9.1 | Mandatory full remote regression suite; incremental migration; single live gate |
| Live changes go untested on real runtime | Consolidated final live GPU acceptance gate (§21.1) |
| CPU work leaks torch into control plane | Import-boundary tests; separate ML runtime |
| CPN-only gate expands scope | Reuse existing detector/checkpoint; no new training; bandwidth-tier output |
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
4. AssetManifest V1 is frozen; `asset_manifest_sha256` is the cryptographic
   asset identity, verified end-to-end, and a ModelRelease merely references it.
5. The canonical request hash scheme, runtime-commit check, atomic ingest,
   terminal immutability, and physical-box validation are frozen.
6. Analysis Package v1 is unchanged; its public execution projection is only
   `executor`, `device`, and a non-sensitive `environment` label.
7. Exactly one result persistence path.
8. Runnable execution is derived from platform-owned, release-specific
   certificates; a plugin cannot self-certify.
9. `local_cpu`/`local_gpu` run in a separate inference worker; the control-plane
   API process never imports torch.
10. Input compatibility uses the recording's dataset label space;
    DetectionResult validation uses the plugin's output label space.
11. Dataset access/layout resolution happens only through a DatasetAdapter; a
    Plugin runtime may read the resolved `RecordingInput.data_path` IQ.
12. Device selection happens only through a RuntimeDescriptor.
13. Trusted asset paths come only from a release/manifest-namespaced deployment
    mapping; wire values are never trusted as paths.
14. Every run resolves a ModelRelease (explicit or platform default, or `None`
    for release-less plugins); availability and executor projections are
    release-specific.
15. Legacy version/task identities remain valid; new plugins are recommended to
    use SemVer, not required.

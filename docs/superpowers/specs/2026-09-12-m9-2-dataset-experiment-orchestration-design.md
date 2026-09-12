# M9.2 Phase G — Dataset Experiment Orchestration Design

Date: 2026-09-12

Status: Design approved; implementation not yet authorized.

## 1. Purpose

Phase G adds dataset-level experiment orchestration on top of the already
validated single-Recording inference path.

Its purpose is to close the WISA V1:

    run -> evaluate -> compare

loop without creating a second inference stack.

Central rule:

> Phase G organizes existing AnalysisRun, Executor, Plugin, DetectionResult,
> and DatasetEvaluation capabilities. It does not replace or bypass them.

Target flow:

    Dataset snapshot
      -> DatasetExperiment
      -> DatasetExperimentItem x N
      -> DatasetExperimentAttempt
      -> existing AnalysisRun
      -> existing Executor
      -> existing Plugin runtime
      -> DetectionResult
      -> existing DatasetEvaluation
      -> AP / mAP / Precision / Recall / F1

## 2. Scope

### In scope

- Freeze one Ground-Truth-bearing dataset snapshot into one experiment.
- Freeze plugin, release, asset-manifest, parameters, executor, runtime,
  evaluation protocol, and experiment concurrency.
- Materialize one logical experiment item per frozen Recording.
- Create AnalysisRuns only when Items obtain execution slots.
- Reuse the existing AnalysisService execution path.
- Support bounded per-experiment concurrency.
- Continue after individual Item failures.
- Support explicit Retry Failed without rerunning successful Items.
- Recover experiment orchestration after platform restart.
- Automatically create formal DatasetEvaluation only after 100% inference
  success.
- Reuse the existing benchmark/evaluation implementation.
- Provide REST endpoints for experiment create/start/read/items/attempts/retry.

### Out of scope

- Model training.
- Phase F-B STFT-YOLO training/integration.
- Global executor scheduler/resource pool.
- Priority, pause, cancel, preemption, fairness.
- Automatic retry policies.
- Transient-error classification.
- Cross-experiment result caching.
- Historical AnalysisRun reuse.
- Generic no-GT batch inference.
- Frontend productization.
- Scientific pipeline changes.
- Metric-definition changes.
- AssetManifest V1 hash changes.
- Canonical remote request hash changes.

## 3. Architectural Principles

### 3.1 Experiment identity and execution identity are separate

DatasetExperiment = one frozen scientific/execution experiment definition.

AnalysisRun = one concrete inference execution.

DatasetExperimentItem = one logical frozen dataset member that must be
processed.

DatasetExperimentAttempt = historical relation between an Item and one concrete
AnalysisRun.

Do not make AnalysisRun dataset-specific.

### 3.2 Logical granularity is not physical batching

WISA retains one logical AnalysisRun per Recording for:

- provenance
- recovery
- retry
- inspection
- evaluation membership

This does not forbid future Executor-level optimization:

- persistent workers
- GPU batching
- model load reuse

Physical optimization belongs below the orchestration identity layer.

### 3.3 No historical AnalysisRun reuse in V1

A new DatasetExperiment owns a newly executed AnalysisRun set.

Do not automatically bind old completed AnalysisRuns, even if apparent
plugin/release/parameter identity matches.

Within one Experiment, already completed Items are preserved across retry.

### 3.4 Retry never revives a terminal AnalysisRun

Retry creates:

    new Attempt
      -> new AnalysisRun

Previous failed/interrupted AnalysisRuns remain immutable history.

### 3.5 Best-effort inference

One Item failure does not stop unrelated queued Items.

When all Items become terminal:

- if any Item failed: Experiment -> completed_with_failures
- if all Items completed: Experiment -> evaluating

### 3.6 No partial automatic formal benchmark

Formal DatasetEvaluation is automatically created only when every frozen Item
has one successful completed AnalysisRun.

## 4. Data Model

Phase G adds three new tables:

- dataset_experiments
- dataset_experiment_items
- dataset_experiment_attempts

Avoid adding DatasetExperiment-specific foreign keys to analysis_runs.

### 4.1 DatasetExperimentModel

Recommended fields:

    id
    name

    dataset_name
    dataset_split
    dataset_label_space
    recording_manifest_hash

    plugin_id
    plugin_version
    model_release_id nullable
    asset_manifest_sha256 nullable

    executor
    runtime_descriptor_json
    parameters_json
    evaluation_protocol
    max_concurrency

    status

    dataset_evaluation_id nullable

    coordinator_token nullable
    worker_pid nullable
    heartbeat_at nullable

    error_type nullable
    error_message nullable

    created_at
    started_at nullable
    completed_at nullable

Frozen after creation:

    dataset_name
    dataset_split
    dataset_label_space
    recording_manifest_hash

    plugin_id
    plugin_version
    model_release_id
    asset_manifest_sha256

    executor
    runtime_descriptor_json
    parameters_json
    evaluation_protocol
    max_concurrency

Do NOT persist duplicate aggregate counters such as:

    queued_items
    running_items
    completed_items
    failed_items

These are derived from Item rows.

### 4.2 DatasetExperimentItemModel

Recommended fields:

    id
    experiment_id

    manifest_order
    recording_id

    status

    last_error_type nullable
    last_error_message nullable

    created_at
    updated_at

Constraints:

    UNIQUE(experiment_id, recording_id)
    UNIQUE(experiment_id, manifest_order)

States:

    queued
    running
    completed
    failed

Do NOT persist a second current_analysis_run_id source of truth.

Current execution is derived from the latest Attempt.

Do NOT persist an `attempt_count` column.

Attempt history authority is the set of DatasetExperimentAttempt rows for the
Item. The next `attempt_number` is derived safely from the existing Attempts as:

    max(attempt_number) + 1

computed under the Item's transaction/concurrency protection (the same
transaction that binds a new Attempt), so a duplicate counter can never drift.

If an API later exposes `attempt_count`, it is a derived read-model value only,
never persisted authority.

### 4.3 DatasetExperimentAttemptModel

Recommended fields:

    id
    experiment_item_id
    attempt_number
    analysis_run_id

    launch_requested_at nullable

    created_at

Constraints:

    UNIQUE(experiment_item_id, attempt_number)
    UNIQUE(analysis_run_id)

Attempt must NOT duplicate:

- executor metadata
- parameters
- status
- inference output
- error payload

Those remain authoritative on AnalysisRun.

## 5. State Machines

### 5.1 DatasetExperiment states

    pending
    running
    completed_with_failures
    evaluating
    completed
    failed

Nominal transitions:

    pending -> running

    running -> completed_with_failures
    running -> evaluating
    running -> failed

    completed_with_failures -> running
      only via explicit Retry Failed

    evaluating -> completed
    evaluating -> failed

The `evaluating` transitions are owned exclusively by the coordinator
evaluating-state reconciliation (section 9), which observes the linked
DatasetEvaluation until it reaches a terminal state. There is no other path that
moves an Experiment out of `evaluating`.

A failed Experiment whose inference is already 100% complete may return to
evaluation only through explicit evaluation retry.

`completed` means:

    complete inference
    +
    completed formal DatasetEvaluation

### 5.2 Item states

    queued -> running
    running -> completed
    running -> failed

    failed -> queued
      only through explicit Retry Failed

Completed Items never return to queued.

### 5.3 AnalysisRun authority

AnalysisRun.status is authoritative for one concrete execution.

DatasetExperimentItem.status is the orchestration projection of the latest
relevant Attempt.

DatasetExperiment.status is the experiment-level state.

DatasetEvaluation.status remains the evaluation-state authority.

The coordinator must never claim Item=completed if the corresponding
AnalysisRun is not completed.

## 6. Experiment Creation

Creating an Experiment freezes identity but does not immediately launch
inference workers.

Creation validates:

1. Dataset snapshot exists and is non-empty.
2. Dataset uses the existing GT-bearing benchmark manifest rules.
3. Exact Plugin identity exists.
4. Plugin parameters are valid.
5. Release-bound / release-less semantics are respected.
6. ModelRelease resolution uses authoritative:

       ModelReleaseStore.resolve(
           plugin_id,
           plugin_version,
           model_release_id
       )

7. Exact AssetManifest SHA is frozen for release-bound Plugins.
8. Requested executor is technically supported.
9. Exact ExecutionCertificate exists for frozen plugin/release/runtime identity.
10. Provider RuntimeDescriptor is available and frozen.
11. max_concurrency >= 1.
12. Exactly one DatasetExperimentItem is created for every frozen manifest
    Recording in manifest order.

Do not perform one expensive remote probe per Recording just to create the
Experiment.

Recording-specific executability is checked when the concrete AnalysisRun is
prepared.

## 7. AnalysisService Prepare / Launch Seam

Current AnalysisService.create_run() combines persistence and worker launch.

Phase G requires an INTERNAL split:

    prepare_run(...)
    launch_prepared_run(...)

Transaction ownership is the core of the seam and MUST be implemented exactly.

### prepare_run(...)

prepare_run(...):

- performs validation/resolution
- constructs and adds the AnalysisRun to the CALLER'S SQLAlchemy Session
- may flush if identity/PK visibility is required
- MUST NOT commit
- MUST NOT launch
- never owns the transaction

### launch_prepared_run(...)

launch_prepared_run(...) launches exactly the already-persisted prepared Run.
It must never create or re-prepare a Run.

### Existing public Single Recording behavior remains

    create_run(...)
      ==
    prepare_run(...)
      -> caller/service commit
      -> launch_prepared_run(...)

### DatasetExperiment orchestration

Dataset orchestration uses ONE transaction to persist, together:

- the prepared AnalysisRun
- the DatasetExperimentAttempt binding
- the ExperimentItem ownership/`running` transition

then commits that single transaction BEFORE launch:

    prepare_run(...)                      # no commit, no launch
      -> add DatasetExperimentAttempt     # binds Item -> AnalysisRun
      -> Item.status = running
      -> commit                           # ONE transaction
      -> mark launch_requested_at
      -> launch_prepared_run(...)

There MUST NOT be a durable committed AnalysisRun that has no durable
DatasetExperimentAttempt ownership. A crash after `prepare_run()` but before the
single commit must leave NO committed AnalysisRun and NO committed Attempt; the
Item remains `queued`/retryable and the whole unit is re-prepared cleanly.

The purpose is reliable ownership before process launch.

This seam MUST NOT change:

- canonical remote request hash
- AssetManifest V1 hashing
- ModelRelease authority
- exact certificate semantics
- runtime descriptor semantics
- terminal AnalysisRun immutability
- current public Single Recording API behavior

## 8. Launch Ambiguity

A crash may happen around worker launch.

Phase G must not blindly double-launch the same AnalysisRun.

DatasetExperimentAttempt therefore carries:

    launch_requested_at

### Local CPU

Recovery case:

    launch_requested_at == NULL
    AnalysisRun.status == pending

First launch is still safe.

Recovery case:

    launch_requested_at != NULL
    AnalysisRun.status == pending

Launch outcome is ambiguous.

V1 MUST fail closed rather than automatically launch the same AnalysisRun
again.

The Item may later be retried using:

    new Attempt
    new AnalysisRun

If the worker did actually start and already moved the Run to:

    running
    completed
    failed

reconciliation respects the real AnalysisRun state.

### Remote GPU

Do not invent a new recovery model.

Reuse existing fenced remote recovery semantics for pending/running remote_gpu
AnalysisRuns.

## 9. DatasetExperiment Coordinator

Each active Experiment has one local coordinator subprocess.

Recommended boundary:

    FastAPI
      -> DatasetExperimentJobManager
      -> python -m app.dataset_experiments.worker
           <experiment_id>
           <coordinator_token>

The coordinator is CONTROL PLANE ONLY.

It must NOT import:

- torch
- ultralytics
- CPN
- ZoomSpec
- model-specific scientific implementations

### Coordinator loop

Each iteration:

1. Verify coordinator_token matches database.
   If not, old coordinator exits.

2. Update heartbeat.

3. Reconcile Item states from latest AnalysisRuns.

4. Verify frozen scientific/execution identity has not drifted.

5. If no queued/running Items remain:

   if failed > 0:
       Experiment -> completed_with_failures
       exit

   if all completed:
       create/start DatasetEvaluation
       Experiment -> evaluating
       (do not exit; continue to evaluation reconciliation)

6. If Experiment.status == evaluating:
   reconcile the linked DatasetEvaluation as specified in
   "Evaluating-state reconciliation" below.
   Never schedule inference in this state.

7. If Experiment.status == running:

       free_slots =
           max_concurrency - active_item_count

8. Select queued Items deterministically by manifest_order.

9. Start at most free_slots Items.

10. Sleep a small bounded polling interval.

11. Repeat.

### Evaluating-state reconciliation

When `Experiment.status == evaluating`, each coordinator iteration MUST run the
following and MUST NOT schedule inference.

1. `dataset_evaluation_id` MUST exist.
   A missing id is an invariant violation: Experiment -> failed, exit.

2. Load the linked DatasetEvaluation.

3. `evaluation.status` in {`pending`, `running`}:
   keep waiting; continue bounded polling.

4. `evaluation.status == completed`:
   Experiment -> completed
   Experiment.completed_at = now
   coordinator exits.

5. `evaluation.status == interrupted`:
   use the existing DatasetBenchmarkService retry semantics to transition the
   evaluation back to `pending` and start it again.
   No AnalysisRun is created.
   Continue polling.

6. `evaluation.status == failed`:
   Experiment -> failed
   preserve all inference results
   do NOT automatically retry
   coordinator exits
   explicit Retry Evaluation may later resume evaluation only.

7. Any missing, corrupt, or mismatched linked DatasetEvaluation:
   fail closed as a DatasetExperiment invariant/orchestration failure
   (section 14.2).

Startup recovery for evaluating Experiments relies on this same logic; the
restarted coordinator re-enters the `evaluating` branch and continues from the
current DatasetEvaluation status.

V1 uses database polling.

Do NOT add:

- Redis
- Celery
- Kafka
- external queue service

## 10. Bounded Concurrency

max_concurrency belongs to DatasetExperiment.

It does not belong to Plugin.

It does not belong to AnalysisRun.

Recommended safe V1 deployment defaults:

    local_cpu  = 1
    remote_gpu = 1

Schema/logic may permit >1 later.

Phase G does NOT implement a global cross-Experiment resource scheduler.

Therefore two simultaneous DatasetExperiments may each consume their own
configured concurrency.

This is accepted V1 behavior.

## 11. Retry Failed

V1 performs no automatic Item retry.

Explicit Retry Failed is valid only from:

    completed_with_failures

It:

- requeues failed Items only
- leaves completed Items unchanged
- preserves every old Attempt
- preserves every old AnalysisRun
- rotates/starts a new Experiment coordinator generation
- creates new Attempts and new AnalysisRuns only when requeued Items obtain
  slots

## 12. Restart Recovery

Database state is the orchestration source of truth.

Retain existing recovery responsibilities.

Recommended startup order:

    1. existing local AnalysisRun stale-run handling
    2. existing DatasetEvaluation stale-run handling
    3. existing remote AnalysisRun recovery
    4. DatasetExperiment recovery

DatasetExperiment recovery:

- does not rerun completed Items
- does not automatically retry failed Items
- rotates fresh coordinator_token
- restarts coordinator for non-terminal running/evaluating Experiments
- reconciles Item state from real AnalysisRun state
- for an `evaluating` Experiment, the restarted coordinator re-enters the
  section 9 evaluating-state reconciliation; it does not require, and must not
  perform, fresh inference.

Old coordinator sees token mismatch and exits.

## 13. Evaluation Integration

Automatically create DatasetEvaluation only when:

    completed_items == expected_items
    failed_items == 0
    queued_items == 0
    running_items == 0

For every Item select its latest successful Attempt's completed AnalysisRun.

Invoke existing DatasetBenchmarkService with:

    recording_manifest_hash =
        frozen Experiment manifest

    items =
        exact successful Experiment membership

    allow_incomplete = False

    evaluation_protocol =
        frozen Experiment evaluation protocol

The existing benchmark subsystem remains responsible for:

- manifest consistency
- one Run per Recording
- completed Run validation
- pipeline id/version consistency
- GT/prediction loading
- localization metrics
- localization AP
- classification applicability
- class-aware AP
- per-class metrics
- confusion matrix
- coverage/comparability

Phase G does NOT reimplement metrics.

The coordinator observes the linked DatasetEvaluation through the
evaluating-state reconciliation in section 9. The transitions
`evaluating -> completed` and `evaluating -> failed` are owned by that
reconciliation; there is no separate transition path. A completed
DatasetEvaluation is never left with an evaluating Experiment: the same
coordinator iteration that observes `evaluation.status == completed` sets
`Experiment.status = completed`.

### Evaluation interruption

If evaluation is interrupted by platform restart, orchestration may reuse the
existing benchmark retry transition and restart evaluation without rerunning
inference.

### Evaluation failure

A genuine benchmark failure does NOT invalidate or rerun inference.

Inference results remain preserved.

Experiment becomes failed at evaluation stage.

Explicit Retry Evaluation retries metric computation only.

## 14. Error Semantics

### 14.1 Item-level execution failure

Examples:

- worker exception
- input incompatibility
- Recording-specific execution failure
- remote execution failure
- run-level asset/deployment failure

Effect:

    AnalysisRun -> failed/interrupted
    Item        -> failed

Other queued Items continue.

### 14.2 Experiment-level invariant failure

Examples:

- PluginVersion identity drift
- ModelRelease / AssetManifest drift
- RuntimeDescriptor drift
- frozen manifest corruption
- duplicate active Attempt
- inconsistent Attempt/Run ownership
- coordinator invariant violation

Effect:

    Experiment -> failed

Stop launching new Items.

Preserve all existing Runs and Results.

Suggested generic error families:

    DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED
    DATASET_EXPERIMENT_INVARIANT_VIOLATION
    DATASET_EXPERIMENT_ORCHESTRATION_FAILED

### 14.3 Evaluation-stage failure

Do not rerun inference.

Retry metrics only.

## 15. Frozen Invariants

The implementation and every review MUST preserve:

1. Frozen Dataset Membership
   - manifest hash
   - Item membership
   - manifest order

2. Frozen Scientific Identity
   - plugin_id
   - plugin_version
   - model_release_id
   - asset_manifest_sha256
   - parameters

3. Frozen Execution Identity
   - executor
   - runtime_descriptor
   - max_concurrency

4. Exact Child Identity
   Every Experiment-created AnalysisRun matches the frozen Experiment.

5. No Historical Run Reuse
   New Experiments do not auto-bind old AnalysisRuns.

6. Terminal AnalysisRun Immutability
   Retry creates a new AnalysisRun.

7. One Active Attempt Per Item
   At most one non-terminal AnalysisRun per Item.

8. Bounded Concurrency
   active Item count <= max_concurrency.

9. Best-effort Inference
   One Item failure does not stop unrelated queued Items.

10. No Partial Formal Benchmark
    failed Item(s) block automatic formal DatasetEvaluation creation.

11. Exact Evaluation Membership
    Exactly one successful Run per frozen Item.

12. No Science in Orchestrator
    No model-specific or heavy ML dependencies.
    GroundTruth does not enter Plugin runtime.

13. Existing M9.2 Execution Contracts Remain Frozen
    - canonical request hash
    - AssetManifest V1 hash
    - exact certificate matching
    - exact release authority
    - terminal immutability

## 16. REST API Surface

V1 endpoints:

    POST /api/dataset-experiments

    POST /api/dataset-experiments/{id}/run

    GET  /api/dataset-experiments

    GET  /api/dataset-experiments/{id}

    GET  /api/dataset-experiments/{id}/items

    GET  /api/dataset-experiments/{id}/items/{item_id}/attempts

    POST /api/dataset-experiments/{id}/retry-failed

    POST /api/dataset-experiments/{id}/retry-evaluation

No Phase G endpoints for:

- pause
- cancel
- delete
- priority

## 17. Additive Migration

Follow the repository's existing V1 additive-migration pattern.

Add focused table creation for:

    dataset_experiments
    dataset_experiment_items
    dataset_experiment_attempts

using SQLAlchemy:

    create(..., checkfirst=True)

Avoid changing AnalysisRun schema unless implementation evidence proves it
necessary and the architect approves that scope change.

## 18. Test Strategy

### 18.1 Model / migration

Verify:

- all three tables
- foreign keys
- unique constraints
- additive migration from existing V1 database

### 18.2 Analysis prepare/launch seam

Verify:

- prepare stages/adds the exact AnalysisRun in the caller-owned transaction
  without commit or launch
- prepare performs no commit and no worker launch
- single-Recording create_run keeps current behavior (prepare -> caller/service
  commit -> launch)
- dataset orchestration binds prepared Run + Attempt + Item ownership transition
  in ONE transaction and commits before launch
- no committed AnalysisRun can exist without a committed Attempt owner
- launch launches exactly the already-persisted prepared run
- launch failure preserves existing error semantics
- local and remote provenance remains unchanged
- release/certificate/runtime identity remains exact

### 18.3 State machine

Verify:

- deterministic manifest-order scheduling
- bounded concurrency
- best-effort execution
- failed terminal set -> completed_with_failures
- 100% success -> evaluating
- evaluating + completed evaluation -> Experiment completed
- evaluating + running evaluation -> remain evaluating
- evaluating + interrupted evaluation -> retry evaluation only
- evaluating + failed evaluation -> Experiment failed
- evaluating + missing evaluation -> fail closed
- none of the evaluating paths create new AnalysisRuns
- partial success never creates formal evaluation

### 18.4 Retry / recovery

Verify:

- Retry Failed requeues failed only
- completed Items never rerun
- retry creates new Attempt + AnalysisRun
- stale coordinator token exits
- ambiguous local launch fails closed
- remote recovery delegates to existing fenced recovery
- restart does not create duplicate active Attempts

### 18.5 Evaluation integration

Verify:

- exact 100% membership
- final successful Attempt chosen
- allow_incomplete=False
- evaluating never schedules inference
- interrupted evaluation can recover without inference rerun
- failed evaluation retry creates no new AnalysisRuns

### 18.6 Required negative cases

    identity drift -> Experiment failed

    manifest drift -> Experiment failed

    duplicate active Attempt -> reject/fail closed

    historical matching AnalysisRun -> ignored

    partial success -> no formal DatasetEvaluation

    old coordinator token -> coordinator exits

    scheduler cannot exceed max_concurrency

## 19. Real Acceptance

Do NOT require the entire SpaceNet test dataset.

Use a small real acceptance snapshot:

    3 real SpaceNet Recordings

    cpn_bandwidth_tier
    plugin_version = 1.0.0
    model_release  = golden

    executor   = local_cpu
    device     = cpu
    precision  = float32

    max_concurrency = 1

Required real path:

    DatasetExperiment
      -> 3 Items
      -> 3 Attempts
      -> 3 real AnalysisRuns
      -> configured separate ML interpreter
      -> real LS-STFT
      -> real CPN
      -> persisted DetectionResults
      -> formal DatasetEvaluation
      -> Experiment completed

Because:

    CPN output label space =
        cpn_bandwidth_tier_v1

while:

    SpaceNet GT label space =
        spacenet_14

acceptance MUST confirm:

- input compatibility succeeds
- localization evaluation succeeds
- classification_applicable == false
- no invalid Narrow/Mid/Wide vs SpaceNet-14 classification comparison

Routine regression tests should prefer Dummy/STFT-Energy fixtures so normal
pytest does not require YOLO or real deployment assets.

## 20. Implementation Gates

Phase G must be implemented incrementally.

### G1 — Persistence and Frozen Experiment

- models
- additive migration
- schemas/read models
- Experiment creation
- frozen manifest Items
- frozen identity validation

### G2 — Analysis Prepare/Launch Seam

- prepare_run
- launch_prepared_run
- unchanged create_run behavior
- local/remote execution regression

### G3 — Orchestrator Core

- Attempt creation
- coordinator worker/job manager
- bounded concurrency
- reconciliation
- best-effort execution

### G4 — Retry and Recovery

- Retry Failed
- launch ambiguity fencing
- coordinator tokens
- restart recovery
- duplicate active Attempt protection

### G5 — Evaluation Integration and API

- full-success evaluation creation
- evaluation lifecycle
- REST endpoints
- read models
- Retry Evaluation

### G6 — Real Acceptance and Seal

- 3 real SpaceNet Recordings
- real certified CPN local CPU
- real DatasetEvaluation
- full backend regression
- architect direct GitHub diff/code audit
- independent review
- Phase G seal

Each gate follows:

    architect prompt
      -> OpenCode TDD implementation
      -> architect direct GitHub audit
      -> independent review where required
      -> gate seal

## 21. Completion Criteria

Phase G is complete only when:

- a frozen DatasetExperiment can be created and started
- it schedules per-Recording AnalysisRuns through existing execution contracts
- concurrency is bounded
- one Item failure does not stop unrelated Items
- Retry Failed reruns failed Items only using new AnalysisRuns
- restart does not rerun completed Items
- restart does not duplicate active Attempts
- execution/scientific identity drift fails closed
- formal DatasetEvaluation occurs only after complete inference coverage
- evaluation retry does not rerun inference
- real 3-Recording CPN acceptance completes end-to-end
- full backend regression remains green
- generic orchestration contains zero model-specific logic

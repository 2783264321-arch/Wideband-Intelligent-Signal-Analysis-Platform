# M9.1 Task 12F-C — Live Remote GPU End-to-End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the final user flow on ONE live SpaceNet recording over the real remote GPU deployment:

```text
SpaceNet recording -> ZoomSpec -> remote_gpu Run
  -> local coordinator -> real SSH AutoDL runner -> RTX5090 inference
  -> download/verify -> SAME AnalysisRun completed
  -> DetectionResult persisted -> frontend renders detections
  -> Algorithm Lab compares run against GT
```

**Type:** Plan-only for the local control plane + the remaining UI surface. Task 12F-C **does NOT** change the remote worker, the wire protocol, the result schema, the ORM, or any 12A-E scientific semantics. The remote half (12F-B) and the control-plane half (12F-A) are already implemented and GPU-accepted; 12F-C drives them together and closes the remaining Spectrum-UI gap.

**Base:** `feature/m9-1-live-remote-gpu-inference @ 6f24f3796efa99ca0c0f1099462f450127f25737`

**Spec/plans:** `docs/superpowers/specs/2026-09-09-m9-1-task12f-remote-platform-integration-design.md` (§1-§6, §11, §12, §14); `docs/superpowers/plans/2026-09-09-m9-1-task12f-a-control-plane-remote-lifecycle.md`; `docs/superpowers/plans/2026-09-09-m9-1-task12f-b-remote-gpu-production-execution.md`.

---

## Scope And Boundaries

**In scope (12F-C only):**
- Real `SshRunner` → remote probe integration gate (card mode).
- Production local `RemoteProfile`/env deployment configuration (API boots with full `WSP_REMOTE_*` env).
- One sample0 HTTP-level availability + `create_run(remote_gpu)`.
- Live local coordinator → real SSH submit/status/download → ingest → SAME `AnalysisRun` `completed`.
- DB `DetectionResult` verification against the real 13-detection package; GET/readback verification.
- Spectrum UI remote pipeline/executor/run/result path (the one confirmed frontend gap).
- Algorithm Lab comparison of the completed remote run against sample0 GT.
- Restart/re-attach acceptance during one live run.
- Negative deployment/unavailable behavior without corrupting existing runs.
- CPU/no-GPU tests (fakes) + clearly separated GPU-required live gates.

**Out of scope (kept frozen):**
- No new result schema, ORM entity, remote protocol, or manual ZIP import.
- No special Algorithm Lab branch for remote runs (compare is executor-agnostic).
- No change to `RemoteExecutionEnvelopeV1` / `RemoteExecutionBatchV1` / worker env bridge / `publish_result` / `ZoomSpecRemoteItemExecutor` / `run_work`.
- No Task12A-E scientific changes (LS-STFT/CPN/AHLP/FRN/postprocess unchanged).
- No Dataset Benchmarks change: live `remote_gpu` runs do **not** flow into the imported-batch benchmark pipeline (unchanged).
- `runner.py`/control-plane module imports remain torch/ultralytics-free.

---

## Fresh Gap Analysis (inspection at base SHA)

### Already works (12F-A/B complete, CPU-tested and/or GPU-accepted)

| Layer | Status |
|---|---|
| `RemoteProfile`/`SshRunner` scalar env bridge + `validate_runner_environment` | ✅ (12F-B Task 1, `test_remote_transport.py`) |
| `runner` `probe`/`submit`/`status`/`work` CLI + `RemoteWorkerContext` | ✅ (12F-B, real probe/execution GPU gate passed) |
| `RemoteExecutorProbe` adapter (`SshRemoteExecutorProbe`) mapping transport/runner responses | ✅ (12F-A, `test_remote_probe_error_channel.py`) |
| `AnalysisService.executor_availability` + `GET /api/executor-availability` | ✅ (12F-A, `test_remote_executor_availability.py`) |
| `AnalysisService.create_run(remote_gpu)` freeze + coordinator launch | ✅ (12F-A, `test_remote_create_run.py`) |
| Coordinator submit→poll→download→ingest→terminal | ✅ (12F-A, `test_remote_coordinator*.py`, fakes) |
| Startup recovery / restart re-attach (`coordinate_orphaned_remote_runs`) | ✅ (12F-A, `test_remote_startup_recovery.py`) |
| `ingest_remote_result` + `AnalysisResultWriter` (SAME run, write-once) | ✅ (12F-A/B, `test_remote_result_ingestor.py`) |
| `_verify_terminal_result` accepts real envelope+ZIP | ✅ (12F-B GPU gate) |
| `GET /api/analysis-runs/{id}/detections`, `GET /api/detections/{id}` | ✅ executor-agnostic (`test_detections.py`) |
| `POST /api/algorithm-lab/compare` over completed runs | ✅ executor-agnostic (`test_algorithm_lab_compare.py`) |
| Frontend detections rendering (SpectrogramViewer, SignalsPage), run polling | ✅ executor-agnostic |
| Frontend Algorithm Lab Case Analysis | ✅ executor-agnostic (`CaseAnalysisView` no executor filter) |

### What 12F-C must add

| Gap | Detail |
|---|---|
| **Real SSH probe gate** | no live `SshRunner → runner probe` integration has been run (post-12F-B gate). Card mode. |
| **Production local RemoteProfile/env config** | no operator run boots the API with the full `WSP_REMOTE_*` env + real `SshRunner`; no live availability. |
| **HTTP live loop** | no test/gate drives `POST create_run` → real coordinator → SSH → ingest → completed on one sample. |
| **13-detection DB verification** | no gate asserts `DetectionResult` rows equal the real package's 13 detections on the SAME run. |
| **Readback of a remote run** | no live `GET`/frontend render of a `remote_gpu` completed run + hardware metadata. |
| **Spectrum UI remote path** | `createAnalysisRun` hardcodes `executor:"local_cpu"`; no `executor-availability` call; Run button disabled for `!cpuSupported`; pipeline types lack `executors_supported`/`recommended_executor`; run `hardware_info_json`/`execution_metadata_json` never rendered. |
| **Algorithm Lab live acceptance** | no live compare of the remote run against sample0 GT (backend is ready). |
| **Restart/re-attach live** | no live restart-during-run demonstration (mechanism is CPU-tested). |
| **Live negative behavior** | no live demo that a broken/unavailable deployment fails closed without corrupting existing runs. |
| **Full-loop CPU test** | no single CPU test drives the real Coordinator + real ingest + real writer to `completed` with a real package and then verifies readback + compare. |

---

## Frozen Contracts (must not change)

- Worker env bridge, `RemoteWorkerContext` fields, `RemoteExecutionBatchV1`/`ItemV1`/`EnvelopeV1` wire schemas.
- `execution.executor="remote_gpu"`, `device="cuda:0"`, `environment=None` in the package; `hardware` from `default_runtime_info_provider`.
- Frozen ZoomSpec: `zoomspec_yolo26n_aug_combined_frn_v3` / `1.0.0` / `spacenet_14` / `parameters={}`.
- Runtime commit = `6f24f3796efa99ca0c0f1099462f450127f25737`; asset manifest = `16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08`.
- `submit` runtime-drift guard and executor fail-before-inference guards (12F-B corrective) stay.

---

## Deployment / Env Contract (for the GPU gates)

Production local `RemoteProfile` is constructed from env (unchanged `RemoteProfile.from_env`). The gate boots the API with:

```text
WSP_REMOTE_PROFILE_NAME=autodl_primary
WSP_REMOTE_HOST=<autodl host>        WSP_REMOTE_PORT=22
WSP_REMOTE_USER=<user>               WSP_REMOTE_SSH_KEY_PATH=<local key>
WSP_REMOTE_KNOWN_HOSTS_PATH=<local known_hosts>
WSP_REMOTE_REPO_ROOT=/root/autodl-tmp/Wideband-Intelligent-Signal-Analysis-Platform
WSP_REMOTE_JOB_ROOT=<gate job root outside repo>
WSP_REMOTE_PYTHON_PATH=/root/miniconda3/bin/python
WSP_REMOTE_REQUIRED_RUNTIME_COMMIT=6f24f3796efa99ca0c0f1099462f450127f25737
WSP_REMOTE_DATASET_ROOTS_JSON={"SpaceNet":"/root/autodl-tmp/SpaceNet_Dataset/advanced"}
WSP_REMOTE_ASSET_PATHS_JSON={"detector_checkpoint":"/root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt",
  "frn_checkpoint":"/root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt",
  "frozen_config":"/root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml",
  "ls_stft_normalization":"/root/autodl-tmp/ZoomSpec/reports/normalization_ls_stft.json"}
WSP_DATABASE_URL=sqlite:///<gate db>
WSP_PROJECT_ROOT/WSP_DATA_ROOT/WSP_LABEL_SPACE_ROOT as usual
```

The remote server env is exactly the 12F-B worker scalar contract and never includes SSH credentials.

---

# TASK 1 — Full Remote Live Loop to Completed (CPU, real ingest/writer)

**First: read before editing**
- `backend/app/remote_execution/coordinator.py` (`Coordinator`, `make_production_writer_factory`).
- `backend/app/remote_execution/result_ingestor.py` (`ingest_remote_result`, `parse_remote_execution_envelope_json`).
- `backend/app/remote_execution/validation.py` (`AnalysisResultWriter.persist`).
- `backend/app/remote_execution/package_publisher.py` + `result_publisher.py` (build a REAL valid package for the fake download).
- `backend/tests/test_remote_coordinator_writer.py`, `backend/tests/test_remote_coordinator.py`, `backend/tests/benchmark_fixture.py`.

**GOAL:** One CPU test drives the REAL coordinator loop to `completed` with a REAL envelope+ZIP download and the REAL `ingest_remote_result` + `AnalysisResultWriter`, then verifies DB readback and Algorithm Lab comparison. No SSH/GPU.

**RED test (`backend/tests/test_remote_live_loop.py`, new):**
- `test_live_loop_remote_run_completes_and_persists`: seed a Recording (name `0`, `dataset_name="SpaceNet"`, `dataset_split="test"`, `label_space="spacenet_14"`, small valid bounds) + its GroundTruth rows; seed a `remote_gpu` AnalysisRun `pending` with frozen metadata (12F-A `freeze_request_provenance` + `coordinator_token`); a `FakeJobManager` whose `submit` records the batch, whose `status` returns `RemoteBatchStatusV1(status="completed")`, and whose `download` materializes a REAL terminal directory built by `publish_result(...)` from a REAL `build_analysis_package_zip(...)` (1-2 `DetectionPayload` within the recording bounds, valid `spacenet_14` labels); run `Coordinator(...).run()` with `ingest=_default_ingest` and `writer_factory=make_production_writer_factory(settings)`; assert result `"completed"`, `run.status=="completed"`, `run.hardware_info_json == envelope.hardware`, and `DetectionResultModel` rows match the payload 1:1 (t/f/class/confidence/scores).
- `test_live_loop_get_readback_returns_detections`: after completion, `GET /api/analysis-runs/{run_id}` returns `status=completed` + `execution_metadata_json` + `hardware_info_json`; `GET /api/analysis-runs/{run_id}/detections` returns the same detections.
- `test_live_loop_algorithm_lab_compares_remote_run`: `POST /api/algorithm-lab/compare` (run vs run or run vs a second completed local run) returns metrics; no executor filter, no crash.
- `test_live_loop_write_once_no_duplicate_rows`: second `Coordinator.run()` (already completed) does not re-ingest / duplicate rows (idempotent no-op path).

**Expected failure (RED):** no such test exists today (coordinator tests use a fake ingestor + fake bytes, never the real persist path to `completed` + readback).

**Minimal implementation:** test-only (no production code change expected; if the loop exposes a defect, fix within 12F-A-owned files and record the real RED→fix→GREEN).

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_live_loop.py -v
```

**Commit checkpoint:** `test: lock live remote end-to-end loop`

---

# TASK 2 — Live Negative / Unavailable Semantics (CPU)

**First: read before editing**
- `backend/app/analysis/service.py` (`executor_availability`, `_create_remote_gpu_run`).
- `backend/app/remote_execution/job_manager.py` (`submit` runtime-drift guard, `status` mapping).
- `backend/app/remote_execution/coordinator.py` (uncertain-submit reconciliation).
- `backend/tests/test_remote_executor_availability.py`, `backend/tests/test_remote_create_run.py`.

**RED test (`backend/tests/test_remote_live_negative.py`, new):**
- `test_probe_unavailable_create_run_rejected_without_run`: probe unavailable → `POST /api/analysis-runs` → `EXECUTOR_UNAVAILABLE`, **no** `remote_gpu` run row created.
- `test_missing_mapping_returns_transport_unavailable`: a probe whose `run_runner` preflight fails (`dataset_roots`/`asset_paths` incomplete) → availability `false`, reason `REMOTE_TRANSPORT_UNAVAILABLE`, API healthy (no 500).
- `test_runtime_drift_submit_is_uncertain_and_reconciles_same_batch`: coordinator with a job manager whose `submit` raises `PlatformError("REMOTE_SUBMIT_FAILED")` without `runner_code` (drift guard) → run stays `pending` (not terminal, not corrupted); a later poll reconciles the SAME batch id.
- `test_negative_deployment_does_not_corrupt_existing_completed_run`: with a completed remote run present, an unavailable deployment → availability `false`, `create_run` rejected, existing completed run + its `DetectionResult` rows unchanged.

**Expected failure (RED):** these HTTP-level negative paths are not covered together today.

**Minimal implementation:** test-only unless a real gap is exposed (record honestly: RED→fix→GREEN or COVERAGE CONFIRMED).

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_live_negative.py -v
```

**Commit checkpoint:** `test: lock live remote negative semantics`

---

# TASK 3 — Production RemoteProfile / Env Wiring (CPU)

**First: read before editing**
- `backend/app/main.py` (`_wire_remote_lifecycle`).
- `backend/app/remote_execution/executor.py` (`SshRemoteExecutorProbe`).
- `backend/tests/test_remote_bootstrap.py`.

**RED test (extend `backend/tests/test_remote_bootstrap.py`):**
- `test_full_remote_env_wires_real_probe_and_coordinator`: `create_app` with the COMPLETE `WSP_REMOTE_*` env (full scalar env + dataset/asset JSON) → `app.state.remote_config_available is True`, `app.state.remote_executor_probe` is a real `SshRemoteExecutorProbe`, `app.state.remote_coordinator_launcher` is a `CoordinatorJobManager`, `runtime_commit_config` == the profile commit.
- `test_partial_remote_env_stays_healthy_and_preserves_runs`: missing dataset/asset JSON (or missing SSH key) → `remote_config_available is False`, probes `None`, health endpoint `200`, and a pre-existing `remote_gpu` `pending`/`running` run is NOT interrupted and NOT re-launched (`coordinate_orphaned_remote_runs` launches nothing).

**Expected failure (RED):** full-env wiring assertion not covered by existing bootstrap tests.

**Minimal implementation:** test-only (wiring already exists); no production change expected.

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_bootstrap.py -v
```

**Commit checkpoint:** `test: lock production remote deployment wiring`

---

# TASK 4 — Spectrum UI Remote GPU Path (frontend)

**First: read before editing**
- `frontend/src/api/client.ts` (`createAnalysisRun` hardcodes `executor:"local_cpu"`; `PipelineDefinitionWire` lacks `executors_supported`/`recommended_executor`; `AnalysisRunWire` has `hardware_info_json` but no `execution_metadata_json`/`created_at`).
- `frontend/src/api/types.ts`.
- `frontend/src/pages/SpectrumAnalysisPage.tsx` (Run button `disabled={!selectedPipeline?.cpuSupported || runActive}`; `runAnalysis()` passes no executor).
- `frontend/src/pages/SpectrumAnalysisPage.test.tsx`, `frontend/src/api/client.test.ts`.

**Decision (LOCKED):** minimal, executor-aware Spectrum path; **no** new page, **no** Algorithm Lab change, **no** Dataset Benchmarks change.

**Implementation (modify frontend):**
1. `client.ts`:
   - `createAnalysisRun(recordingId, pipelineId, executor = "local_cpu")` — send `executor` in the POST body.
   - new `getExecutorAvailability(recordingId, pipelineId)` → `GET /api/executor-availability` mapped to an `ExecutorAvailability` type (`executor/available/reasonCode/reasonMessage/remoteProfile/recommended`).
   - include `executors_supported` + `recommended_executor` in `PipelineDefinitionWire`/`PipelineDefinition`.
   - add `executionMetadata` + `createdAt` to `AnalysisRun` (wire + mapping).
2. `SpectrumAnalysisPage.tsx`:
   - On pipeline selection, when `executors_supported` includes `remote_gpu`, call `getExecutorAvailability`; render an availability indicator (available/reason, remote profile).
   - Enable "Run Analysis" when `cpu_supported` (local) OR (`remote_gpu` supported AND availability `available`); disable with the reason message otherwise.
   - Pass `executor` to `createAnalysisRun`: `"remote_gpu"` when the recommended/available remote path is selected, else `"local_cpu"` (when `cpu_supported`).
   - Render the current run's `hardwareInfo` + `executionMetadata` (executor/device/profile/timestamps) on the completed run.

**RED test (extend `frontend/src/pages/SpectrumAnalysisPage.test.tsx` + `frontend/src/api/client.test.ts`):**
- `test_run_analysis_sends_remote_gpu_executor`: selecting the ZoomSpec (remote-only) pipeline with availability `available` posts `{executor:"remote_gpu", parameters:{}}`.
- `test_run_button_uses_availability_reason`: remote-only pipeline + availability `false` → button disabled with reason message.
- `test_client_executor_availability_maps_response`: `getExecutorAvailability` maps `available/reason_code/remote_profile`.
- keep existing local-cpu fixture behavior green.

**Expected failure (RED):** current `createAnalysisRun` cannot send `remote_gpu`; no availability call; remote-only pipeline button disabled.

**Focused verification:**
```bash
cd frontend && npm test -- --run src/pages/SpectrumAnalysisPage.test.tsx src/api/client.test.ts
```

**Commit checkpoint:** `feat: surface remote gpu executor in spectrum analysis`

---

# TASK 5 — Algorithm Lab Remote-Run Coverage (CPU)

**First: read before editing**
- `backend/app/evaluation/service.py` (`compare` — executor-agnostic).
- `backend/tests/test_algorithm_lab_compare.py` (fixtures use `local_cpu`/`imported`).
- `backend/tests/benchmark_fixture.py`.

**Decision (LOCKED):** Algorithm Lab is already executor-agnostic; **no production branch** and **no frontend change**. Add one CPU test proving a `remote_gpu` completed run compares identically to any completed run.

**RED test (extend `backend/tests/test_algorithm_lab_compare.py`):**
- `test_compare_includes_remote_gpu_completed_run`: seed a completed run with `executor="remote_gpu"` + detection rows; `POST /api/algorithm-lab/compare` against a second completed run (or run vs GT via the existing single-run semantics) returns metrics; assert the service never filters by executor.

**Expected failure (RED):** no remote-executor fixture in the algorithm-lab tests today.

**Minimal implementation:** test-only.

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_algorithm_lab_compare.py -v
```

**Commit checkpoint:** `test: lock algorithm lab remote run comparison`

---

# TASK 6 — GPU REQUIRED: Live E2E Gate A (real SSH probe + live run to completed)

> **Card mode required.** Operator switches the AutoDL server to card mode (RTX 5090, `nvidia-smi` shows a GPU, `torch.cuda.is_available()` True). Steps below are executed as an acceptance gate, not committed as unit tests.

**Gate script (written at execution time under `/tmp`, never committed):** seeds the sample0 Recording + GT rows (exact 12F-B identity), boots the API with the full env above, and drives HTTP.

1. **Real SSH probe integration gate:**
   ```bash
   # local SshRunner -> fixed scalar env bridge -> remote runner probe
   env $(grep -v '^#' /tmp/opencode/gate_env.sh) \
     "$REPO_ROOT/.venv/bin/python" -m pytest backend/tests/test_remote_bootstrap.py -v
   # plus a direct probe availability over the real profile:
   python - <<'PY'   # uses app.state.remote_executor_probe.availability(recording, zoomspec_def, sha)
   ...
   PY
   ```
   Require: real `SshRunner` probe returns `RemoteProbeResponseV1` `available=true`, `remote_runtime_commit=6f24f37…`, `asset_manifest_sha256=16cc0534…`, `device=0`; `GET /api/executor-availability?recording_id=<sn0>&pipeline_id=zoomspec_yolo26n_aug_combined_frn_v3` → `available=true`, `remote_profile=autodl_primary`, `recommended=true`.

2. **Seed sample0 (gate seeding script, outside repo):**
   - Recording row: `id="rec_sn0"`, `name="0"`, `data_format="float16_interleaved_le"`, `sample_rate_hz=50e6`, `center_frequency_hz=2455e6`, `frequency_low_hz=2430e6`, `frequency_high_hz=2480e6`, `num_samples=7_500_000`, `duration_s=0.15`, `dataset_name="SpaceNet"`, `dataset_split="test"`, `label_space="spacenet_14"`, `has_ground_truth=True`, `external_path=/root/autodl-tmp/SpaceNet_Dataset/advanced/test/0.bin`.
   - GroundTruth rows = the 6 signals in `test/0.json` order (signal 0..5) with exact `start_time/1000`, `end_time/1000`, `start_frequency*1e6`, `end_frequency*1e6`, `class_id`/`class_name` from `spacenet_14`.
   - Assert locally computed `recording_fingerprint == 35deea81c82f706d88c84079b40c377c01a77393821233467bc45b212fbc48d6` and `source_data_sha256 == cb4981e2debfa0eb31f565ed8cff2b259e0330103de85700faff5428145d9b4f` BEFORE creating the run.

3. **HTTP-level availability + create_run:**
   ```bash
   curl -s "http://127.0.0.1:8000/api/executor-availability?recording_id=rec_sn0&pipeline_id=zoomspec_yolo26n_aug_combined_frn_v3"
   curl -s -X POST "http://127.0.0.1:8000/api/analysis-runs" \
     -H 'Content-Type: application/json' \
     -d '{"recording_id":"rec_sn0","pipeline_id":"zoomspec_yolo26n_aug_combined_frn_v3","executor":"remote_gpu","parameters":{}}'
   ```
   Require: run `pending` with `execution_metadata_json.request_sha256` valid, `worker_pid` set (coordinator subprocess alive).

4. **Live coordinator → completed:** poll `GET /api/analysis-runs/{id}` until `completed`; assert `hardware_info_json.device_name` = RTX 5090, `execution_metadata_json.payload_sha256` matches the downloaded zip.

5. **DB DetectionResult verification vs the real 13-detection package:** `GET /api/analysis-runs/{id}/detections` returns **13** rows; cross-check against `analysis_result.zip` `detections.json` (13), and the 12F-B gate's package summary (Zigbee/SRRC/LoRa/WiFi/BLE classes present).

6. **GET/readback:** `GET /api/analysis-runs/{id}` + detections render through the Spectrum UI (see Task 4) with hardware metadata visible.

7. **Algorithm Lab live acceptance:** `POST /api/algorithm-lab/compare {recording_id:"rec_sn0", run_a_id:<remote>, run_b_id:<remote or second completed>}` returns detection/class metrics; no crash, no special branch.

**FINAL SAFETY after gate:** `git status --short` clean; no repo file changes; no commit/push for this gate.

---

# TASK 7 — GPU REQUIRED: Live E2E Gate B (restart/re-attach + negative behavior)

> **Card mode required.** Same environment as Gate A.

1. **Restart/re-attach during a live GPU job:**
   - `POST /api/analysis-runs` (sample0) and immediately terminate the API process while the remote item is still `running` (remote job survives).
   - Restart the API with the same env → startup `coordinate_orphaned_remote_runs` finds the `remote_gpu` `pending/running` run, rotates the coordinator token, relaunches the coordinator.
   - Assert the SAME run reaches `completed` exactly once; `DetectionResult` rows == 13; no duplicate ingest (write-once; `payload_sha256` identical).

2. **Negative deployment / unavailable behavior (live):**
   - With an existing completed remote run present, restart the API with an intentionally incomplete asset map (or wrong runtime commit) → `GET /api/executor-availability` → `available=false` with explicit reason; `POST /api/analysis-runs` → `EXECUTOR_UNAVAILABLE`; the pre-existing completed run + its detection rows are unchanged (no corruption).
   - (Optional deterministic case) Runtime-drift guard: freeze a run, then point the profile at a different required commit and restart → coordinator `submit` fails `REMOTE_SUBMIT_FAILED` without `runner_code` → run remains `pending` (uncertain submit) and is reconciled against the SAME batch id; it never spawns under the new runtime.

**FINAL SAFETY:** `git status --short` clean; no repo file changes; no commit/push for this gate.

---

## Self-Review

- **Real SSH probe gate:** Task 6 step 1 exercises local `SshRunner` → scalar env bridge → remote `runner probe` (post-12F-B integration gate); the API availability endpoint consumes the real probe result.
- **Production local RemoteProfile/env deployment config:** Task 3 (CPU wiring) + Gate A step 1/2 (real env boot); `RemoteProfile.from_env` stays the single config owner; no new config file.
- **One sample0 HTTP availability + create_run:** Gate A step 3.
- **Coordinator/status/ingest to completed:** Task 1 (CPU full loop, real ingest/writer) + Gate A step 4.
- **DB DetectionResult vs the 13-detection package:** Task 1 (1:1 row/payload) + Gate A step 5 (13 rows).
- **GET/readback:** Task 1 + Gate A step 6; frontend Task 4 renders hardware/metadata.
- **Spectrum UI remote path:** Task 4 is the ONLY frontend work; detections rendering and polling are already executor-agnostic; Dataset Benchmarks/import UI unchanged.
- **Algorithm Lab comparison:** Task 5 (CPU proof) + Gate A step 7 (live); executor-agnostic by design; no special branch.
- **Restart/re-attach:** Gate B step 1 live; mechanism CPU-covered by `test_remote_startup_recovery.py` + coordinator fencing tests.
- **Negative behavior without corrupting runs:** Task 2 (CPU) + Gate B step 2 (live); runtime-drift → uncertain submit reconciles the SAME batch (12F-B corrective preserved).
- **CPU/no-GPU vs GPU-required split:** Tasks 1-5 are CPU/no-GPU (fakes/monkeypatch, no SSH/GPU); Tasks 6-7 are card-mode gates with explicit commands.
- **No new schema/ORM/protocol/manual ZIP import; no Algorithm Lab branch; no 12A-E change; runner import stays GPU-free.**

---

## CPU/no-GPU Test Matrix (Tasks 1-5)

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_remote_live_loop.py \
  backend/tests/test_remote_live_negative.py \
  backend/tests/test_remote_bootstrap.py \
  backend/tests/test_algorithm_lab_compare.py \
  backend/tests/test_remote_create_run.py \
  backend/tests/test_remote_coordinator.py \
  backend/tests/test_remote_coordinator_writer.py \
  backend/tests/test_remote_startup_recovery.py \
  backend/tests/test_remote_result_ingestor.py -v
```

## GPU-REQUIRED Gates (Tasks 6-7 — card mode only)

Listed in Task 6 / Task 7 with explicit commands. Operator switches AutoDL to card mode first; these are acceptance gates, not committed unit tests.

---

## GPU-Required Verification Checklist (operator card mode)

- [ ] `nvidia-smi` shows RTX 5090 (not the 2 GiB reduced container); `torch.cuda.is_available()` True.
- [ ] Real SSH probe returns `available=true` (Gate A step 1).
- [ ] API availability + create_run succeed on sample0 (Gate A step 3).
- [ ] Live coordinator completes the SAME run; 13 `DetectionResult` rows (Gate A step 4-5).
- [ ] Algorithm Lab compares the remote run against sample0 GT (Gate A step 7).
- [ ] Restart/re-attach completes once with no duplicate ingest (Gate B step 1).
- [ ] Broken deployment fails closed without corrupting existing runs (Gate B step 2).
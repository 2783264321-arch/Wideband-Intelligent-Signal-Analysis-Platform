# Phase G G6 — Real Acceptance + Final Seal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development for the single test-only seal-hardening
> checkpoint, and superpowers:verification-before-completion before every
> acceptance claim. This is an acceptance/verification phase, not feature work.

**Goal (G6 sub-gate):** Prove the complete Phase G `run -> evaluate -> completed`
loop on REAL hardware with REAL science: 3 real SpaceNet Recordings,
`cpn_bandwidth_tier` `1.0.0` / `golden`, `local_cpu` on CPU/float32,
`max_concurrency=1`, real LS-STFT + real CPN inference, persisted
`DetectionResult`s, automatic formal `DatasetEvaluation`, and
`DatasetExperiment.status == completed`, while proving
`classification_applicable == false` and localization evaluation succeeded.

**Architecture:** No architecture changes. G6 executes the sealed G1-G5 stack and
produces an acceptance evidence artifact. The only code added is TEST-ONLY
(one seal-hardening regression) and acceptance tooling scripts. If real
execution requires any production change, G6 STOPS.

**Spec:** `docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md` §19, §20 G6.

**Base:** `feature/m9-2-implementation @ 3d9f47b3e9ccbdd4147cf858712202396c256d8d`.

---

## Global Constraints

1. REAL SpaceNet IQ, REAL LS-STFT, REAL CPN `golden` artifact, REAL
   `DetectionResult`s, REAL `DatasetEvaluation`. No substitutes (no DummyPipeline,
   no stft_energy, no synthetic IQ, no fake GT, no mock predictions, no manual
   terminal-state writes).
2. Isolation: a NEW `DatasetExperiment`, NEW Attempts, NEW `AnalysisRun`s. No
   historical run reuse, no historical `AnalysisRun` binding.
3. `executor=local_cpu`, `device=cpu`, `precision=float32`, `max_concurrency=1`.
4. Exactly 3 Items / 3 Attempts / 3 new Runs.
5. Formal evaluation only at 100% inference success; `allow_incomplete=False`.
6. `classification_applicable == false`; no Narrow/Mid/Wide ↔ SpaceNet-14
   classification comparison.
7. No production code change; no schema/migration change; no remote/GPU; no
   frontend; no G6 scope creep.
8. Do not copy IQ data; register via existing external-path semantics.
9. Do not commit DBs, IQ data, model binaries, caches, or large logs.
10. Memory safety: one CPN worker at a time; STOP if headroom is insufficient.

---

## CURRENT G5 SEALED BASE

- branch: `feature/m9-2-implementation`
- HEAD: `3d9f47b3e9ccbdd4147cf858712202396c256d8d`
- worktree: clean
- G5 full suite: `1679 passed, 28 skipped, 0 failed, 0 errors`.

---

## PRE-FLIGHT AUDIT (actual findings)

```text
branch            feature/m9-2-implementation
HEAD              3d9f47b3e9ccbdd4147cf858712202396c256d8d
worktree          clean
python            /root/autodl-tmp/WISA-m9-2-implementation/.venv/bin/python (3.12.3)
memory.max        2147483648  (2 GiB cgroup)
memory.current    2024706048  (~1.89 GiB)  <-- CRITICAL: ~120 MiB headroom
memory.stat       anon 1522225152, file 394272768, slab 95804784
top RSS           opencode 1,276,092 KiB; jupyter-lab 106,860; autopanel 109,652; tensorboard 79,280
platform.db       exists, 0 recordings registered
```

STOP gate MEM-1: G6 execution MUST NOT start unless
`memory.max - memory.current >= 1073741824` (~1 GiB headroom) at launch time. See
MEMORY SAFETY PLAN.

---

## CPN PLUGIN / RELEASE AUDIT

- `backend/app/pipelines/cpn_bandwidth_tier/definition.py`:
  `id=cpn_bandwidth_tier`, `version=1.0.0`, `label_space=cpn_bandwidth_tier_v1`,
  `task_capability=detection_classification`, `input_compatibility=("spacenet_14",)`,
  `output_label_space=cpn_bandwidth_tier_v1`, `model_release_required=True`,
  technical capabilities include `ExecutionCapability("local_cpu","cpu","float32")`.
- `asset_manifest.json`: `asset_manifest_sha256 =
  7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb`; assets
  `detector_checkpoint` (`eba4fa4b...`) and `ls_stft_normalization`
  (`9b994655...`).
- `model_releases/golden.json`: release `golden`, same manifest SHA.
- `model_release_defaults.json`: `cpn_bandwidth_tier/1.0.0 -> golden`.
- `ModelReleaseStore.resolve("cpn_bandwidth_tier","1.0.0","golden")` is expected
  to return the golden release + manifest. Verified by
  `test_cpn_asset_identity_and_model_release_exact` (runs with real assets).

STOP gate RELEASE-1: resolved release id != `golden` or resolved manifest SHA !=
`7ab8a6a4...` or asset digests != recorded SHAs.

---

## EXECUTION CERTIFICATE AUDIT

`backend/app/pipelines/execution_certificates.json` contains:

```text
plugin_id        = cpn_bandwidth_tier
plugin_version   = 1.0.0
model_release_id = golden
executor         = local_cpu
device_type      = cpu
precision        = float32
runtime_ref      = local:autodl_primary:cpu:a1237f8faae7
evidence_ref     = m9_2_fa2_cpn_local_cpu_acceptance
```

`ExecutorRegistry.certified_capability` binds the certificate to the registered
provider's ACTUAL `runtime_descriptor()`; CPN declares the matching
`local_cpu/cpu/float32` technical capability, so certification succeeds when the
local provider is registered. `executor_registry` never substitutes a different
executor.

STOP gate CERT-1: `certified_capability(cpn, "golden", "local_cpu")` is `None`,
or the certificate tuple differs from the above.

---

## LOCAL CPU RUNTIME AUDIT

- Provider config (`backend/app/analysis/local_executor.py`): a `local_cpu`
  provider is registered ONLY when BOTH `WSP_LOCAL_CPU_PYTHON_PATH` and
  `WSP_LOCAL_CPU_RUNTIME_REF` are set.
- Current shell has NO `WSP_*` variables, so `build_local_providers(settings)`
  returns `{}` and CPN `local_cpu` is NOT certified by default. G6 MUST launch
  the app with the local CPU env configured.
- Interpreter present: `/root/miniconda3/bin/python -> python3.12`.
- `runtime_descriptor()` → `local_cpu / cpu / None / float32`,
  `environment_ref=/root/miniconda3/bin/python`,
  `environment_label=local:autodl_primary:cpu:a1237f8faae7` (must match the
  certificate `runtime_ref`).
- Worker entrypoint: `python -m app.analysis.local_inference_worker <run_id>`,
  spawned by `LocalInferenceWorkerProvider.launch` with the configured interpreter
  and `WSP_LOCAL_INFERENCE_RUNTIME_REF` = provider `runtime_ref`.

STOP gate RUNTIME-1: interpreter missing/not executable; `runtime_descriptor`
does not equal `local_cpu/cpu/float32` with the exact `runtime_ref`; generation
fingerprint `a1237f8faae7` mismatch (re-run acceptance + re-certify instead).

---

## ML INTERPRETER / ASSET AUDIT

- Interpreter (`docs/research/m9_2_cpn_local_cpu_acceptance.md`): Python 3.12.3,
  torch 2.8.0+cu128 (CPU used, `torch.cuda.is_available() == False`),
  ultralytics 8.4.114, numpy 2.3.2, scipy 1.18.0.
- Assets present:
  - `/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt` (5,388,613 B)
  - `/root/autodl-tmp/release/support/normalization_ls_stft.json` (5,115 B)
- Namespaced asset env:
  `WSP_LOCAL_ASSET_PATHS_JSON = {"cpn_bandwidth_tier/1.0.0/<manifest_sha>":
  {"detector_checkpoint": "<pt>", "ls_stft_normalization": "<json>"}}`.
- CPN science entrypoint: `app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3`
  (`build_ls_stft_spectrogram`, `CPNDetector`) wrapped by
  `CPNBandwidthTierPipeline`.

STOP gate ASSET-1: either asset missing; SHA256 mismatch vs the frozen
AssetManifest; interpreter lacks torch/ultralytics; worker import failure.

---

## SPACENET DATA AUDIT

- Root: `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/` — 2500 `.bin` +
  2500 `.json` pairs.
- JSON contract: `observation_range` `[lo_mhz, hi_mhz]`; `signals[]` with
  `start_frequency`/`end_frequency` (MHz), `start_time`/`end_time` (ms),
  `class` (SpaceNet-14 id). Adapter: `app/datasets/spacenet.py`.
- Registration semantics: `app/datasets/service.py::SpaceNetRegistrationService`
  reads `.bin`+`.json`, resolves the REAL file path, and inserts a
  `RecordingModel` with `external_path` + `source="spacenet"` +
  `has_ground_truth=bool(signals)` and `GroundTruthModel` rows — WITHOUT copying
  IQ. The public API `POST /api/datasets/spacenet/register` registers the WHOLE
  split (2500). G6 MUST NOT register the whole split.
- `platform.db` currently has 0 recordings, so any G6 registration fully defines
  the SpaceNet manifest.

STOP gate DATA-1: chosen file missing; `.bin`/`.json` pair missing; zero signals;
adapter rejects the sample; `external_path` not an existing regular file.

---

## THREE-RECORDING SELECTION STRATEGY

Deterministic selection: the first three valid GT-bearing stems by stable
ascending numeric stem order restricted to the advanced test split: `0`, `1`,
`2`. Verified metadata:

| manifest_order | recording name | external_path | data_format | sample_rate_hz | center_frequency_hz | num_samples | duration_s | GT count | GT classes |
|---|---|---|---|---|---|---|---|---|---|
| 0 | `0` | `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/0.bin` | `float16_interleaved_le` | 50,000,000 | 2,455,000,000 | 7,500,000 | 0.1500 | 6 | 3,7,8,9,10 |
| 1 | `1` | `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/1.bin` | `float16_interleaved_le` | 50,000,000 | 2,443,000,000 | 5,000,000 | 0.1000 | 10 | 2,4,5,6,7,8,9,10,12,13 |
| 2 | `2` | `/root/autodl-tmp/SpaceNet_Dataset/advanced/test/2.bin` | `float16_interleaved_le` | 20,000,000 | 2,421,000,000 | 1,200,000 | 0.0600 | 6 | 8,9,10,12 |

`dataset_name=SpaceNet`, `dataset_split=test`, `label_space=spacenet_14`,
`has_ground_truth=True`. Total 13,700,000 samples (~0.31 s of IQ). Manifest order
is by `RecordingModel.name` ascending (`0`,`1`,`2`).

STOP gate SELECT-1: any stem missing GT, or fewer/more than 3 GT-bearing
recordings present in the SpaceNet manifest at experiment creation.

---

## MEMORY SAFETY PLAN

- Inspect at runtime; never assume:
  `cat /sys/fs/cgroup/memory.max` and `/sys/fs/cgroup/memory.current`.
- At plan time: max `2147483648`, current `2024706048` → ~120 MiB headroom.
- STOP gate MEM-1 (pre-launch, mandatory):
  `headroom = memory.max - memory.current; require headroom >= 1073741824` (~1 GiB).
  If below: STOP and free memory (stop `jupyter-lab`, `tensorboard`, `autopanel`;
  reduce concurrent tooling) or raise `memory.max`; then re-check. Do NOT proceed.
- Runtime guardrail: `max_concurrency=1` (one CPN worker at a time). Never launch
  additional CPN work while an `AnalysisRun` is `pending`/`running`.
- Monitor during acceptance: sample `memory.current` every 5 s into the evidence
  log; if it exceeds `memory.max - 268435456` (256 MiB), STOP the coordinator and
  treat as MEM-2 failure.
- The coordinator spawn (control plane) is lightweight; only the CPN worker
  (miniconda + torch + ultralytics) is memory-heavy.

STOP gate MEM-2: any OOM kill, `MemoryError`, or worker exit without terminal
status during acceptance.

---

## G5 SEAL-HARDENING TEST PLAN (Task 3, TEST-ONLY)

Add ONE focused regression test to
`backend/tests/test_dataset_experiment_coordinator_evaluating.py` (it targets the
`running -> evaluating` all-success handoff `_finish_inference`, where the link
has committed and a post-link error must be projected under evaluating
ownership with a generation check):

```python
def test_post_link_failure_projection_stale_generation_returns_fence_lost(client, monkeypatch):
    session, ds, analysis, provider, experiment = _all_success_experiment(client)

    def rotate_then_raise(self, experiment_id, evaluation_id, coordinator_token, job_manager):
        # Another Session rotates T1 -> T2 AFTER the link committed (Experiment is
        # already evaluating) and BEFORE the failure projection runs.
        with client.app.state.database.session_factory() as other:
            stored = other.get(DatasetExperimentModel, experiment_id)
            stored.coordinator_token = "T2"
            other.commit()
        raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION", "post-link", 409)

    monkeypatch.setattr(DatasetExperimentService, "start_linked_evaluation", rotate_then_raise)

    coordinator = _coordinator(
        client, provider=provider, job_manager=RecordingBenchmarkJobManager()
    )
    outcome = coordinator.step(experiment.id, "T")

    # `_finish_inference` must observe that the evaluating-guarded failure
    # projection lost the generation and return FENCE_LOST (not INVARIANT_FAILED).
    assert outcome == CoordinatorOutcome.FENCE_LOST
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.coordinator_token == "T2"      # newer generation untouched
        assert stored.status == "evaluating"
```

Expected: GREEN with NO production change if the G5 control-plane correction was
implemented. If this test is RED, it exposes a production defect → STOP G6 and
report `G6 ACCEPTANCE BLOCKED BY DEFECT-2` (do NOT silently fix G5 here).

Red/Green:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_coordinator_evaluating.py::test_post_link_failure_projection_stale_generation_returns_fence_lost -v
```

---

## REAL DATASETEXPERIMENT EXECUTION PLAN

### Fixed acceptance identity

```text
plugin_id        = cpn_bandwidth_tier
plugin_version   = 1.0.0
model_release_id = golden
executor         = local_cpu
evaluation_protocol = physical_tf_detection_ap_v2
max_concurrency  = 1
```

### Acceptance runtime environment (exported before launching the app/driver)

```bash
cd /root/autodl-tmp/WISA-m9-2-implementation
export WSP_PROJECT_ROOT="$PWD"
export WSP_DATA_ROOT="$PWD/data"
export WSP_LABEL_SPACE_ROOT="$PWD/label_spaces"
export WSP_DATABASE_URL="sqlite:///$PWD/g6_acceptance.db"
export WSP_LOCAL_CPU_PYTHON_PATH="/root/miniconda3/bin/python"
export WSP_LOCAL_CPU_RUNTIME_REF="local:autodl_primary:cpu:a1237f8faae7"
export WSP_LOCAL_INFERENCE_WORK_ROOT="$PWD/data/g6_work"
export WSP_LOCAL_ASSET_PATHS_JSON='{"cpn_bandwidth_tier/1.0.0/7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb":{"detector_checkpoint":"/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt","ls_stft_normalization":"/root/autodl-tmp/release/support/normalization_ls_stft.json"}}'
mkdir -p "$WSP_DATA_ROOT" "$WSP_LOCAL_INFERENCE_WORK_ROOT"
rm -f "$PWD/g6_acceptance.db"
```

Rationale: `DatasetExperimentJobManager` and
`LocalInferenceWorkerProvider.launch` inherit `os.environ`; the coordinator and
the CPN worker therefore receive the exact local CPU runtime + namespaced assets.

### Registration (exact script, reuses existing external-path semantics)

`scripts/g6_register_spacenet_three.py` (acceptance tooling, not production):

```python
from pathlib import Path
from app.datasets.spacenet import SpaceNetAdapter
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel
from app.core.config import Settings
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from uuid import uuid4

STEMS = ("0", "1", "2")
REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/root/autodl-tmp/SpaceNet_Dataset/advanced")

settings = Settings()
database = Database(settings.database_url)
load_domain_models()
Base.metadata.create_all(database.engine)
run_additive_migrations(database.engine)

adapter = SpaceNetAdapter(DATA_ROOT, REPO / "label_spaces", "spacenet_14")
with database.session_factory() as session:
    for stem in STEMS:
        sample = adapter.load("test", stem)
        external_path = str(sample.data_path.resolve())
        recording_id = f"rec_{uuid4().hex}"
        session.add(RecordingModel(
            id=recording_id, name=sample.id, data_path=external_path,
            data_format=sample.data_format, source="spacenet",
            external_path=external_path, sample_rate_hz=sample.sample_rate_hz,
            center_frequency_hz=sample.center_frequency_hz,
            frequency_low_hz=sample.frequency_low_hz,
            frequency_high_hz=sample.frequency_high_hz,
            num_samples=sample.num_samples, duration_s=sample.duration_s,
            dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", has_ground_truth=True,
        ))
        for signal in sample.signals:
            session.add(GroundTruthModel(
                id=f"gt_{uuid4().hex}", recording_id=recording_id,
                t_start_s=signal.t_start_s, t_end_s=signal.t_end_s,
                f_low_hz=signal.f_low_hz, f_high_hz=signal.f_high_hz,
                class_id=signal.class_id, class_name=signal.class_name,
            ))
    session.commit()
```

Run:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/g6_register_spacenet_three.py
```

### Experiment creation + run (exact driver)

`scripts/g6_acceptance_driver.py` (acceptance tooling, not production):

```python
import json, os, time
from pathlib import Path
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "data" / "g6_work" / "g6_evidence.json"

# MEM-1 pre-launch gate
mem_max = int(Path("/sys/fs/cgroup/memory.max").read_text())
mem_now = int(Path("/sys/fs/cgroup/memory.current").read_text())
if mem_max - mem_now < 1073741824:
    raise SystemExit(f"STOP MEM-1: headroom {mem_max - mem_now} < 1 GiB")

from app.main import create_app
from app.core.config import Settings
from app.db.session import Database
from app.analysis.model import AnalysisRunModel
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel, DatasetExperimentItemModel, DatasetExperimentModel,
)
from app.benchmarks.model import DatasetEvaluationModel, DatasetEvaluationItemModel
from app.detections.model import DetectionResultModel
from app.recordings.model import RecordingModel

settings = Settings()
database = Database(settings.database_url)

with database.session_factory() as session:
    rec_ids = [r.id for r in session.query(RecordingModel).all()]
    pre_run_ids = {
        run.id for run in session.query(AnalysisRunModel)
        .filter(AnalysisRunModel.recording_id.in_(rec_ids)).all()
    }

app = create_app(settings)
client = TestClient(app)
payload = {
    "name": "g6-real-acceptance",
    "dataset_name": "SpaceNet", "dataset_split": "test",
    "dataset_label_space": "spacenet_14",
    "plugin_id": "cpn_bandwidth_tier", "plugin_version": "1.0.0",
    "model_release_id": "golden", "executor": "local_cpu",
    "parameters": {}, "evaluation_protocol": "physical_tf_detection_ap_v2",
    "max_concurrency": 1,
}
created = client.post("/api/dataset-experiments", json=payload)
created.raise_for_status()
experiment_id = created.json()["id"]
run_response = client.post(f"/api/dataset-experiments/{experiment_id}/run")
run_response.raise_for_status()

deadline = time.time() + 3600
status = None
while time.time() < deadline:
    with database.session_factory() as session:
        status = session.get(DatasetExperimentModel, experiment_id).status
    if status in {"completed", "failed", "completed_with_failures"}:
        break
    time.sleep(2)

evidence = {
    "base_sha": "3d9f47b3e9ccbdd4147cf858712202396c256d8d",
    "experiment_id": experiment_id,
    "pre_run_ids": sorted(pre_run_ids),
    "final_experiment_status": status,
}
with database.session_factory() as session:
    exp = session.get(DatasetExperimentModel, experiment_id)
    evidence["dataset_evaluation_id"] = exp.dataset_evaluation_id
    items = list(
        session.query(DatasetExperimentItemModel)
        .filter_by(experiment_id=experiment_id)
        .order_by(DatasetExperimentItemModel.manifest_order)
        .all()
    )
    evidence["items"] = []
    runs = []
    for item in items:
        attempts = list(
            session.query(DatasetExperimentAttemptModel)
            .filter_by(experiment_item_id=item.id)
            .order_by(DatasetExperimentAttemptModel.attempt_number)
            .all()
        )
        evidence["items"].append({
            "item_id": item.id,
            "manifest_order": item.manifest_order,
            "status": item.status,
            "attempts": [
                {"attempt_id": a.id, "attempt_number": a.attempt_number,
                 "analysis_run_id": a.analysis_run_id,
                 "launch_requested_at": str(a.launch_requested_at)}
                for a in attempts
            ],
        })
        for a in attempts:
            run = session.get(AnalysisRunModel, a.analysis_run_id)
            runs.append({
                "run_id": run.id,
                "status": run.status,
                "executor": run.executor,
                "detections": session.query(DetectionResultModel)
                .filter_by(run_id=run.id).count(),
            })
    evidence["runs"] = runs
    if exp.dataset_evaluation_id:
        ev = session.get(DatasetEvaluationModel, exp.dataset_evaluation_id)
        agg = ev.aggregate_metrics_json or {}
        evidence["evaluation"] = {
            "id": ev.id,
            "status": ev.status,
            "expected_recordings": ev.expected_recordings,
            "evaluated_recordings": ev.evaluated_recordings,
            "missing_recordings": ev.missing_recordings,
            "coverage": ev.coverage,
            "included_items": session.query(DatasetEvaluationItemModel)
            .filter_by(evaluation_id=ev.id, status="included").count(),
            "classification_applicable": agg.get("classification_applicable"),
            "classification_reason": agg.get("classification_reason"),
            "localization": agg.get("localization"),
            "per_class_metrics_json": ev.per_class_metrics_json,
        }
EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str))
print(json.dumps(evidence, indent=2, default=str))
```

The driver MUST assert the acceptance conditions before writing evidence:

```python
assert status == "completed", f"experiment terminal status {status}"
assert len(evidence["items"]) == 3
assert sum(len(i["attempts"]) for i in evidence["items"]) == 3
assert len(evidence["runs"]) == 3
assert all(r["status"] == "completed" and r["executor"] == "local_cpu"
           for r in evidence["runs"])
assert evidence["evaluation"]["status"] == "completed"
assert evidence["evaluation"]["coverage"] == 1.0
assert evidence["evaluation"]["missing_recordings"] == 0
assert evidence["evaluation"]["included_items"] == 3
assert evidence["evaluation"]["classification_applicable"] is False
assert evidence["evaluation"]["classification_reason"] == "label_space_mismatch"
```

STOP gate RUN-1: `POST /run` != 202; experiment terminal `failed` or
`completed_with_failures`; timeout; any `AnalysisRun` failed/interrupted.

---

## REAL ANALYSISRUN EVIDENCE PLAN

Exact inspection (run with the control-plane interpreter; the DB path comes from
`WSP_DATABASE_URL`):

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" - <<'PY'
from app.core.config import Settings
from app.db.session import Database
from app.dataset_experiments.model import (
    DatasetExperimentModel, DatasetExperimentItemModel, DatasetExperimentAttemptModel,
)
from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel
s = Settings(); db = Database(s.database_url)
with db.session_factory() as session:
    exp = session.query(DatasetExperimentModel).filter_by(name="g6-real-acceptance").one()
    print("experiment", exp.id, exp.status, exp.dataset_evaluation_id)
    items = session.query(DatasetExperimentItemModel).filter_by(experiment_id=exp.id).order_by(DatasetExperimentItemModel.manifest_order).all()
    for item in items:
        attempts = session.query(DatasetExperimentAttemptModel).filter_by(experiment_item_id=item.id).order_by(DatasetExperimentAttemptModel.attempt_number).all()
        for a in attempts:
            run = session.get(AnalysisRunModel, a.analysis_run_id)
            n = session.query(DetectionResultModel).filter_by(run_id=run.id).count()
            print("item", item.id, "order", item.manifest_order, "attempt", a.id, a.attempt_number,
                  "run", run.id, run.status, "executor", run.executor, "detections", n)
PY
```

Evidence required: exactly 3 Items; exactly 3 Attempts; exactly 3 new Runs (all
not in the pre-run id snapshot); every `AnalysisRun.status == "completed"`;
`executor == "local_cpu"`; every run has `DetectionResult` count >= 0 persisted
(real inference; a legitimate zero-detection recording is allowed but MUST be
explained; a missing DetectionResult table write for a completed run is a STOP).

STOP gate RUN-2: <3 items/attempts/runs; any reused historical run; any
non-completed run; a completed run whose detection rows were never written.

---

## DATASETEVALUATION EVIDENCE PLAN

Exact inspection:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" - <<'PY'
from app.core.config import Settings
from app.db.session import Database
from app.dataset_experiments.model import DatasetExperimentModel
from app.benchmarks.model import DatasetEvaluationModel, DatasetEvaluationItemModel
s = Settings(); db = Database(s.database_url)
with db.session_factory() as session:
    exp = session.query(DatasetExperimentModel).filter_by(name="g6-real-acceptance").one()
    print("experiment_status", exp.status, "eval_id", exp.dataset_evaluation_id)
    ev = session.get(DatasetEvaluationModel, exp.dataset_evaluation_id)
    print("eval_status", ev.status, "expected", ev.expected_recordings,
          "evaluated", ev.evaluated_recordings, "missing", ev.missing_recordings,
          "coverage", ev.coverage, "manifest", ev.recording_manifest_hash)
    items = session.query(DatasetEvaluationItemModel).filter_by(evaluation_id=ev.id).all()
    print("included", sum(1 for i in items if i.status == "included"), "of", len(items))
    agg = ev.aggregate_metrics_json or {}
    print("classification_applicable", agg.get("classification_applicable"))
    print("classification_reason", agg.get("classification_reason"))
    print("localization", agg.get("localization"))
PY
```

Required evidence: `dataset_evaluation_id` non-NULL; exactly 3 linked evaluation
items, all `status == "included"`; `expected_recordings == 3`;
`missing_recordings == 0`; `coverage == 1.0`; `evaluation.status == "completed"`;
`Experiment.status == "completed"`; `localization` metrics present (e.g.
`localization.ap50`, `ap50_95`, operating tp/fp/fn).

STOP gate EVAL-1: missing evaluation; not 3 included items; coverage != 1.0;
missing != 0; evaluation != completed; Experiment != completed; localization
metrics absent/non-numeric.

---

## CLASSIFICATION-INAPPLICABILITY CHECK

Required evidence:

```text
aggregate_metrics_json["classification_applicable"] == False
aggregate_metrics_json["classification_reason"] == "label_space_mismatch"
aggregate_metrics_json["classification_on_matched"] is None
aggregate_metrics_json["class_aware"] is None
per_class_metrics_json == [] (classification not applicable)
```

Rationale: CPN declares `label_space = cpn_bandwidth_tier_v1`; the frozen
recordings are `spacenet_14`; `classification_applicability` returns
`(False, "label_space_mismatch")`. No per-class SpaceNet-14 comparison against
Narrow/Mid/Wide is produced.

STOP gate CLS-1: `classification_applicable == True`, or any class-aware
comparison is present, or `classification_reason != "label_space_mismatch"`.

Negative-class-comparison proof: `per_class_metrics_json == []` and
`class_aware is None`; additionally the evaluation membership classes are CPN
tier ids `{0,1,2}` while GT classes are SpaceNet-14 ids, so no class id overlap
is treated as classification.

---

## FAILURE / STOP GATES

Any of the following STOPs G6 (do not silently repair; do not alter scientific
identity; report `G6 ACCEPTANCE BLOCKED BY <reason>`):

```text
MEM-1 memory headroom < 1 GiB at launch
MEM-2 OOM / MemoryError / worker exit without terminal status
DATA-1 missing .bin/.json, zero GT, adapter rejects, external_path not a file
SELECT-1 not exactly 3 GT-bearing recordings in the manifest
RELEASE-1 golden missing/mismatched; asset manifest SHA mismatch
ASSET-1 asset file missing; asset SHA mismatch; interpreter lacks torch/ultralytics
CERT-1 exact local_cpu certificate missing
RUNTIME-1 interpreter missing; runtime_descriptor/runtime_ref mismatch; fingerprint mismatch
RUN-1 run endpoint error; experiment failed/completed_with_failures; timeout; run not completed
RUN-2 missing/mismatched item/attempt/run; historical run reuse; missing detections
EVAL-1 missing/failed/incomplete evaluation; Experiment not completed
CLS-1 classification incorrectly attempted / class-aware comparison present
REMOTE-1 any remote_gpu/GPU path or SSH attempted
SCHEMA-1 any schema/migration change appears required
DEFECT-1 any production change required for the real path
DEFECT-2 the G5 seal-hardening test cannot pass without a production change
```

If any STOP triggers: leave the acceptance feature branch committed as-is (do
NOT merge/rewrite), record the exact failure, and end with
`G6 ACCEPTANCE BLOCKED BY <reason>` rather than sealing.

---

## FULL REGRESSION PLAN

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```

Require a fresh summary with 0 failed / 0 errors. Then:

```bash
git diff --check
git status --short
git log --oneline 3d9f47b3e9ccbdd4147cf858712202396c256d8d..HEAD
git diff --stat 3d9f47b3e9ccbdd4147cf858712202396c256d8d..HEAD
```

Scope scan (must be empty for forbidden paths):

```bash
git diff --name-only 3d9f47b3e9ccbdd4147cf858712202396c256d8d..HEAD \
  | grep -E "model.py|app/db|migrations|remote_execution|frontend|app/evaluation|app/analysis" || echo "SCOPE_OK"
```

Do NOT commit `g6_acceptance.db`, IQ, model binaries, `data/g6_work`, or caches.

---

## ACCEPTANCE EVIDENCE ARTIFACT

Create `docs/superpowers/acceptance/2026-09-13-phase-g-g6-real-acceptance.md`
containing:

```text
base SHA (5e90aea / 3d9f47b)
final SHA
server/runtime identity (platform, python, torch, ultralytics, cpu)
memory.max / peak memory.current
selected 3 recordings (ids, names, external_path, sizes, GT counts)
manifest hash
plugin/release/asset identity (manifest SHA + asset SHAs)
ExecutionCertificate tuple
runtime descriptor + interpreter
experiment ID
item IDs (3) + manifest_order
attempt IDs (3) + attempt_number
AnalysisRun IDs (3) + status + detection counts
DatasetEvaluation ID + status + coverage + missing
localization metric summary
classification_applicable evidence
full pytest result
scope audit
known limitations
```

`docs/superpowers/acceptance/` is a NEW directory; the artifact is the only file
committed there. No logs/data/binaries.

---

## TASK DECOMPOSITION

### Task 1 — Acceptance Environment Audit (verification only)

- Run every preflight command in this plan; capture outputs.
- Enforce MEM-1 before any CPN launch.
- STOP if any audit gate fails.
- No commit unless the G6 plan doc itself is unchanged (this task produces a
  report only; if a code blocker is found, STOP).

### Task 2 — Three-Recording Acceptance Snapshot (acceptance tooling + read-only audit)

- Create `scripts/g6_register_spacenet_three.py` (exact content above).
- Reset `g6_acceptance.db`; register stems 0/1/2.
- Verify 3 recordings + GT counts; capture `prepare_manifest` hash.
- Commit: `test: add G6 three-recording registration tooling` (script only).

### Task 3 — G5 Seal-Hardening Regression (TEST-ONLY)

- Add `test_post_link_failure_projection_stale_generation_returns_fence_lost`.
- RED only if a defect exists; otherwise GREEN with no production change.
- Focused: `pytest backend/tests/test_dataset_experiment_evaluation_ownership.py -q`.
- Commit: `test: add G6 post-link stale-generation seal hardening`.
- STOP on DEFECT-2.

### Task 4 — Create and Start the Real DatasetExperiment (real execution)

- Run `scripts/g6_acceptance_driver.py` with the exact env above.
- Record experiment id, frozen identity, and pre-run run-id snapshot.
- Commit: none (execution only); the evidence artifact lands in Task 7.

### Task 5 — Execute Three Real Runs (real execution)

- Poll to terminal inference state; capture per-item/attempt/run evidence.
- Verify 3 new runs, all completed, DetectionResults persisted.
- No terminal-state mutation.
- Commit: none.

### Task 6 — Formal DatasetEvaluation Verification (real execution)

- Verify evaluation identity/membership/coverage/status, localization metrics,
  `classification_applicable == false`, and Experiment `completed`.
- STOP on EVAL-1/CLS-1.
- Commit: none.

### Task 7 — Full Regression + Final Phase G Audit + Evidence (verification + doc)

- Run full backend pytest; run `git diff --check` and scope scan.
- Write `docs/superpowers/acceptance/2026-09-13-phase-g-g6-real-acceptance.md`.
- Commit: `docs: add phase G G6 real acceptance evidence`.

---

## Verification Commands (index)

```bash
# preflight
cd /root/autodl-tmp/WISA-m9-2-implementation
git branch --show-current; git rev-parse HEAD; git status --short
cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/memory.current
"$PWD/.venv/bin/python" --version
ls -la /root/miniconda3/bin/python
sha256sum /root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt
sha256sum /root/autodl-tmp/release/support/normalization_ls_stft.json

# focused G5 seal-hardening
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_evaluation_ownership.py::test_post_link_failure_projection_stale_generation_returns_fence_lost -v

# registration
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/g6_register_spacenet_three.py

# real experiment driver (env must be exported first)
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/g6_acceptance_driver.py

# item/attempt/run + detection inspection (see REAL ANALYSISRUN EVIDENCE PLAN)
# evaluation inspection (see DATASETEVALUATION EVIDENCE PLAN)
# full regression
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
git diff --check
git status --short
```

---

## Scope Audit

Expected G6 commits touch only:

```text
docs/superpowers/plans/2026-09-13-phase-g-g6-real-acceptance.md   (this plan)
scripts/g6_register_spacenet_three.py                             (Task 2)
scripts/g6_acceptance_driver.py                                   (Task 4)
backend/tests/test_dataset_experiment_evaluation_ownership.py     (Task 3, test-only)
docs/superpowers/acceptance/2026-09-13-phase-g-g6-real-acceptance.md (Task 7)
```

No production Python, no models, no `app/db`, no migrations, no
`remote_execution/*`, no frontend, no metric/science definitions, no IQ/model
binaries, no DBs.

---

## Self-Review

1. Exactly 3 real SpaceNet recordings selected deterministically and GT-bearing. PASS
2. Registration reuses existing external-path semantics; no IQ copied. PASS
3. Golden release / AssetManifest / asset SHAs audited and STOP-gated. PASS
4. Exact local_cpu ExecutionCertificate + runtime_ref audited. PASS
5. Separate ML interpreter + assets audited. PASS
6. Memory safety: MEM-1 headroom gate + max_concurrency=1 + monitoring. PASS
7. G5 seal-hardening is test-only; STOP on any production defect. PASS
8. Real path creates NEW experiment/attempts/runs; historical reuse forbidden. PASS
9. Evidence captured via the Item -> Attempt -> Run chain, not timestamps. PASS
10. Evaluation identity/membership/coverage/status verified. PASS
11. `classification_applicable == false` and no class-aware comparison. PASS
12. Failure/STOP gates enumerated; no scientific shortcuts. PASS
13. Full regression + scope scan + evidence artifact defined. PASS
14. No TODO/TBD/placeholder in plan semantics; scripts specified. PASS

---

## Verification

- `git diff --check` clean after the plan-only commit.
- Only the G6 plan document changes in this pass.
- Placeholder scan NONE.
- Real execution remains gated by MEM-1 at run time.

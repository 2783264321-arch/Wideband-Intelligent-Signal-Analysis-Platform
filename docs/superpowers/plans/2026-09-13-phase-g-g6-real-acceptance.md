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

`scripts/g6_register_spacenet_three.py` (acceptance tooling, not production). It
fails closed BEFORE any DB write and re-verifies AFTER commit:

```python
"""G6 acceptance tooling: register exactly 3 real SpaceNet Recordings (no IQ copy)."""
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func

from app.benchmarks.service import DatasetBenchmarkService
from app.core.config import Settings
from app.datasets.spacenet import SpaceNetAdapter
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

STEMS = ("0", "1", "2")
REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/root/autodl-tmp/SpaceNet_Dataset/advanced")

settings = Settings()
database = Database(settings.database_url)
load_domain_models()
Base.metadata.create_all(database.engine)
run_additive_migrations(database.engine)

adapter = SpaceNetAdapter(DATA_ROOT, REPO / "label_spaces", "spacenet_14")

# Fail closed BEFORE any DB write.
for stem in STEMS:
    sample = adapter.load("test", stem)  # raises on missing/invalid pair
    if not sample.signals:
        raise SystemExit(f"STOP DATA-1: {stem} has zero GT signals")
    if not sample.data_path.is_file():
        raise SystemExit(f"STOP DATA-1: {sample.data_path} is not a file")

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

# Re-verify AFTER commit from a fresh Session.
with database.session_factory() as session:
    recordings = list(session.query(RecordingModel).filter_by(
        dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14").all())
    if len(recordings) != 3:
        raise SystemExit(f"STOP DATA-1: expected 3 SpaceNet recordings, found {len(recordings)}")
    if {r.name for r in recordings} != set(STEMS):
        raise SystemExit(f"STOP DATA-1: names {sorted(r.name for r in recordings)} != {list(STEMS)}")
    seen = []
    for recording in recordings:
        if not recording.external_path or not Path(recording.external_path).is_file():
            raise SystemExit(f"STOP DATA-1: external_path not a regular file for {recording.name}")
        if not recording.has_ground_truth:
            raise SystemExit(f"STOP DATA-1: {recording.name} has_ground_truth is False")
        gt_count = int(session.query(func.count(GroundTruthModel.id))
                       .filter_by(recording_id=recording.id).scalar() or 0)
        if gt_count <= 0:
            raise SystemExit(f"STOP DATA-1: {recording.name} has zero GT rows")
        seen.append({
            "recording_id": recording.id, "name": recording.name,
            "external_path": recording.external_path,
            "sample_rate_hz": recording.sample_rate_hz,
            "center_frequency_hz": recording.center_frequency_hz,
            "num_samples": recording.num_samples, "duration_s": recording.duration_s,
            "gt_count": gt_count,
        })
    preview = DatasetBenchmarkService(session).prepare_manifest(
        "SpaceNet", "test", "spacenet_14")
    if preview.expected_recordings != 3:
        raise SystemExit(
            f"STOP DATA-1: manifest expected_recordings {preview.expected_recordings} != 3")
    ordered = [(entry.manifest_order, entry.recording_name) for entry in preview.entries]
    if ordered != [(0, "0"), (1, "1"), (2, "2")]:
        raise SystemExit(f"STOP DATA-1: manifest order {ordered} not deterministic")
    print(json.dumps({
        "recordings": sorted(seen, key=lambda item: item["name"]),
        "manifest_hash": preview.recording_manifest_hash,
        "manifest_order": ordered,
    }, indent=2, default=str))
```

Run:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/g6_register_spacenet_three.py
```

### Experiment creation + run (exact driver, ONE complete executable script)

`scripts/g6_acceptance_driver.py` (acceptance tooling, not production). It
implements MEM-1, MEM-2 monitoring, acceptance-owned cleanup, evidence
collection, and ALL acceptance assertions BEFORE writing success evidence:

```python
"""G6 real acceptance driver: MEM-1/MEM-2, real run, fail-closed assertions."""
import json
import os
import signal
import time
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "data" / "g6_work" / "g6_evidence.json"
MEM_MAX_PATH = Path("/sys/fs/cgroup/memory.max")
MEM_NOW_PATH = Path("/sys/fs/cgroup/memory.current")
GIB = 1073741824
MEM2_RESERVE = 268435456  # 256 MiB
POLL_TIMEOUT_S = 3600


def read_mem(path):
    text = path.read_text().strip()
    return int(text) if text.isdigit() else (1 << 62)


mem_max = read_mem(MEM_MAX_PATH)
mem_initial = read_mem(MEM_NOW_PATH)
if mem_max - mem_initial < GIB:
    raise SystemExit(
        f"G6 ACCEPTANCE BLOCKED BY MEM-1: headroom {mem_max - mem_initial} < 1 GiB")

from app.main import create_app
from app.core.config import Settings
from app.db.session import Database
from app.analysis.model import AnalysisRunModel
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel, DatasetExperimentItemModel, DatasetExperimentModel,
)
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.detections.model import DetectionResultModel
from app.recordings.model import RecordingModel

settings = Settings()
database = Database(settings.database_url)


def read_proc_argv(pid):
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    if not raw:
        return None
    return [
        part.decode("utf-8", errors="replace")
        for part in raw.split(b"\0")
        if part
    ]


def is_local_run_worker(argv, run_id):
    if not argv:
        return False
    try:
        index = argv.index("-m")
    except ValueError:
        return False
    return (
        len(argv) > index + 2
        and argv[index + 1] == "app.analysis.local_inference_worker"
        and argv[index + 2] == run_id
    )


def is_dataset_experiment_worker(argv, experiment_id, coordinator_token):
    if not argv:
        return False
    try:
        module_index = argv.index("-m")
        token_flag_index = argv.index("--coordinator-token")
    except ValueError:
        return False
    return (
        len(argv) > module_index + 2
        and argv[module_index + 1] == "app.dataset_experiments.worker"
        and argv[module_index + 2] == experiment_id
        and len(argv) > token_flag_index + 1
        and argv[token_flag_index + 1] == coordinator_token
    )


def acceptance_owned_processes(experiment_id):
    """Return (coordinator_pid_or_None, sorted inference_worker_pids).

    Ownership requires BOTH active DB state AND exact live /proc/<pid>/cmdline
    identity. Terminal rows never contribute; missing/mismatched /proc is skipped
    (fail closed).
    """
    coordinator = None
    inference_workers = []
    with database.session_factory() as session:
        experiment = session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            return coordinator, inference_workers
        if (
            experiment.status in {"running", "evaluating"}
            and experiment.worker_pid is not None
            and experiment.coordinator_token is not None
        ):
            pid = int(experiment.worker_pid)
            if is_dataset_experiment_worker(
                read_proc_argv(pid), experiment.id, experiment.coordinator_token
            ):
                coordinator = pid
        items = session.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment_id).all()
        for item in items:
            attempts = session.query(DatasetExperimentAttemptModel).filter_by(
                experiment_item_id=item.id).all()
            for attempt in attempts:
                run = session.get(AnalysisRunModel, attempt.analysis_run_id)
                if (
                    run is not None
                    and run.status in {"pending", "running"}
                    and run.worker_pid is not None
                ):
                    pid = int(run.worker_pid)
                    if is_local_run_worker(read_proc_argv(pid), run.id):
                        inference_workers.append(pid)
    return coordinator, sorted(set(inference_workers))


def safe_sigterm(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def cleanup_acceptance_processes(experiment_id):
    """SIGTERM only currently active, exact-identity G6 processes.

    Stops the validated coordinator FIRST so it cannot schedule more work, then
    the validated active local inference workers. Never mutates DB state and
    never signals any PID whose live argv identity cannot be proven.
    """
    coordinator, inference_workers = acceptance_owned_processes(experiment_id)
    if coordinator is not None:
        safe_sigterm(coordinator)
    for pid in inference_workers:
        if pid != coordinator:
            safe_sigterm(pid)


# pre-run snapshot (run-isolation evidence)
with database.session_factory() as session:
    recording_ids = [recording.id for recording in session.query(RecordingModel).all()]
    pre_run_ids = {
        run.id for run in session.query(AnalysisRunModel)
        .filter(AnalysisRunModel.recording_id.in_(recording_ids)).all()
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
if created.status_code != 201:
    raise SystemExit(f"STOP RUN-1: create -> {created.status_code} {created.text}")
experiment_id = created.json()["id"]
run_response = client.post(f"/api/dataset-experiments/{experiment_id}/run")
if run_response.status_code != 202:
    raise SystemExit(f"STOP RUN-1: run -> {run_response.status_code} {run_response.text}")

deadline = time.time() + POLL_TIMEOUT_S
status = None
mem_peak = mem_initial
mem2_triggered = False
next_mem_sample = time.time()
while time.time() < deadline:
    with database.session_factory() as session:
        experiment = session.get(DatasetExperimentModel, experiment_id)
        status = experiment.status if experiment is not None else None
    now = time.time()
    if now >= next_mem_sample:
        current = read_mem(MEM_NOW_PATH)
        mem_peak = max(mem_peak, current)
        if current > mem_max - MEM2_RESERVE:
            mem2_triggered = True
            break
        next_mem_sample = now + 5
    if status in {"completed", "failed", "completed_with_failures"}:
        break
    time.sleep(1)

mem_final = read_mem(MEM_NOW_PATH)
mem_peak = max(mem_peak, mem_final)

if mem2_triggered:
    cleanup_acceptance_processes(experiment_id)
    raise SystemExit(
        f"G6 ACCEPTANCE BLOCKED BY MEM-2: peak {mem_peak} > {mem_max - MEM2_RESERVE}")

if status not in {"completed", "failed", "completed_with_failures"}:
    cleanup_acceptance_processes(experiment_id)
    raise SystemExit("G6 ACCEPTANCE BLOCKED BY RUN-1: timeout")

# collect evidence
evidence = {
    "base_sha": "3d9f47b3e9ccbdd4147cf858712202396c256d8d",
    "experiment_id": experiment_id,
    "pre_run_ids": sorted(pre_run_ids),
    "final_experiment_status": status,
    "memory": {
        "max": mem_max, "initial": mem_initial, "peak": mem_peak,
        "final": mem_final, "mem1_ok": True, "mem2_triggered": False,
    },
}
with database.session_factory() as session:
    experiment = session.get(DatasetExperimentModel, experiment_id)
    evidence["dataset_evaluation_id"] = experiment.dataset_evaluation_id
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
        for attempt in attempts:
            run = session.get(AnalysisRunModel, attempt.analysis_run_id)
            runs.append({
                "run_id": run.id,
                "status": run.status,
                "executor": run.executor,
                "worker_pid": run.worker_pid,
                "detections": session.query(DetectionResultModel)
                .filter_by(run_id=run.id).count(),
            })
    evidence["runs"] = runs
    if experiment.dataset_evaluation_id:
        evaluation = session.get(DatasetEvaluationModel, experiment.dataset_evaluation_id)
        aggregate = evaluation.aggregate_metrics_json or {}
        evidence["evaluation"] = {
            "id": evaluation.id,
            "status": evaluation.status,
            "expected_recordings": evaluation.expected_recordings,
            "evaluated_recordings": evaluation.evaluated_recordings,
            "missing_recordings": evaluation.missing_recordings,
            "coverage": evaluation.coverage,
            "included_items": session.query(DatasetEvaluationItemModel)
            .filter_by(evaluation_id=evaluation.id, status="included").count(),
            "classification_applicable": aggregate.get("classification_applicable"),
            "classification_reason": aggregate.get("classification_reason"),
            "classification_on_matched": aggregate.get("classification_on_matched"),
            "class_aware": aggregate.get("class_aware"),
            "localization": aggregate.get("localization"),
            "per_class_metrics_json": evaluation.per_class_metrics_json,
        }

# ALL acceptance assertions BEFORE writing success evidence.
assert status == "completed", f"experiment terminal status {status}"
assert len(evidence["items"]) == 3, evidence["items"]
assert sum(len(i["attempts"]) for i in evidence["items"]) == 3
assert len(evidence["runs"]) == 3, evidence["runs"]
assert all(r["status"] == "completed" and r["executor"] == "local_cpu"
           for r in evidence["runs"]), evidence["runs"]
assert all(r["run_id"] not in pre_run_ids for r in evidence["runs"]), "historical run reuse"
assert len({r["run_id"] for r in evidence["runs"]}) == 3
assert evidence["dataset_evaluation_id"] is not None
assert evidence["evaluation"]["status"] == "completed"
assert evidence["evaluation"]["expected_recordings"] == 3
assert evidence["evaluation"]["evaluated_recordings"] == 3
assert evidence["evaluation"]["coverage"] == 1.0
assert evidence["evaluation"]["missing_recordings"] == 0
assert evidence["evaluation"]["included_items"] == 3
assert evidence["evaluation"]["localization"] is not None
assert isinstance(evidence["evaluation"]["localization"]["ap50"], (int, float))
assert isinstance(evidence["evaluation"]["localization"]["ap50_95"], (int, float))
assert evidence["evaluation"]["classification_applicable"] is False
assert evidence["evaluation"]["classification_reason"] == "label_space_mismatch"
assert evidence["evaluation"]["classification_on_matched"] is None
assert evidence["evaluation"]["class_aware"] is None
assert evidence["evaluation"]["per_class_metrics_json"] == []

EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str))
print(json.dumps(evidence, indent=2, default=str))
```

STOP gate RUN-1: `POST /run` != 202; experiment terminal `failed` or
`completed_with_failures`; timeout (acceptance-owned PID cleanup then non-zero
exit); any `AnalysisRun` failed/interrupted. Later startup stale recovery owns DB
reconciliation of any interrupted workers.

---

## ACCEPTANCE-OWNED PROCESS CLEANUP (FAIL-CLOSED)

The driver's `cleanup_acceptance_processes(experiment_id)` (called by both the
MEM-2 and timeout paths) is the ONLY cleanup mechanism. Ownership requires BOTH
current active DB state AND exact live `/proc/<pid>/cmdline` identity:

```text
AnalysisRun candidate:
    run.status in {"pending", "running"} AND run.worker_pid is not None
    argv must contain: -m  app.analysis.local_inference_worker  <exact run_id>

DatasetExperiment coordinator candidate:
    experiment.status in {"running", "evaluating"}
    AND experiment.worker_pid is not None
    AND experiment.coordinator_token is not None
    argv must contain: -m  app.dataset_experiments.worker  <exact experiment_id>
                       --coordinator-token  <exact coordinator_token>
```

Order: validated coordinator FIRST (stop orchestration), then validated active
inference workers (dedup; never re-signal the coordinator PID).

Fail-closed rules (all SKIP, never kill):

```text
terminal DB row (completed/failed/interrupted) -> not a candidate
/proc/<pid> missing or unreadable -> skip
argv identity mismatch (exact run id / experiment id / token) -> skip
permission denied -> skip
PID alone or process name alone or substring match -> NEVER sufficient
```

`cleanup_acceptance_processes` NEVER mutates
DatasetExperiment/Item/Attempt/AnalysisRun/DatasetEvaluation status, NEVER
kills OpenCode/jupyter-lab/tensorboard/autopanel or any unrelated process, and
never broadens the match. Later startup stale recovery owns DB reconciliation.

Safety invariant:

```text
terminal row            -> never killed
stale/reused PID        -> argv mismatch -> never killed
missing /proc entry     -> skip
permission denied       -> skip
active exact coordinator-> SIGTERM allowed
active exact worker     -> SIGTERM allowed
```

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
base SHA                 3d9f47b3e9ccbdd4147cf858712202396c256d8d (sealed G5 production base)
final SHA                <final HEAD>
G6 plan/tooling SHAs     <plan + acceptance-tooling commits; NOT the production base>
server/runtime identity  platform, python, torch, ultralytics, cpu
memory                   memory.max, initial memory.current, peak memory.current,
                         final memory.current, MEM-1 result, MEM-2 result
selected 3 recordings    recording_id, name, external_path, sample_rate_hz,
                         center_frequency_hz, num_samples, duration_s, GT count,
                         manifest_order
manifest hash            recording_manifest_hash
plugin/release/asset     plugin_id/version/release, manifest SHA, asset SHAs
certificate              exact ExecutionCertificate tuple
runtime descriptor       local_cpu/cpu/float32 + environment_label/interpreter
experiment ID
item IDs                 (3) + manifest_order
attempt IDs              (3) + attempt_number
AnalysisRun IDs          (3) + status + executor + per-run DetectionResult count
run isolation            pre_run_ids, experiment-owned final run IDs, and
                         set(final_run_ids) & set(pre_run_ids) == empty
DatasetEvaluation ID     + status + expected/evaluated/missing/coverage + included_items
localization summary     ap50, ap50_95, operating tp/fp/fn
classification evidence  classification_applicable, classification_reason,
                         classification_on_matched, class_aware, per_class_metrics_json
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

- Create `scripts/g6_register_spacenet_three.py` (exact fail-closed content above).
- Syntax/import check, review, THEN commit the script.
- Commit: `test: add G6 three-recording registration tooling` (script only).
- Reset `g6_acceptance.db`; register stems 0/1/2; capture recording fields, GT
  counts, and `prepare_manifest` hash/order. STOP on DATA-1/SELECT-1.

### Task 3 — G5 Seal-Hardening Regression (TEST-ONLY)

- Add `test_post_link_failure_projection_stale_generation_returns_fence_lost` to
  `backend/tests/test_dataset_experiment_coordinator_evaluating.py`.
- RED only if a defect exists; otherwise GREEN with no production change.
- Focused: `pytest backend/tests/test_dataset_experiment_coordinator_evaluating.py -q`.
- Commit: `test: add G6 post-link stale-generation seal hardening`.
- STOP on DEFECT-2.

### Task 4 — Create + Commit the Acceptance Driver, THEN Execute (tooling + real execution)

- Create `scripts/g6_acceptance_driver.py` (exact complete content above).
- Syntax/import check and review against this plan BEFORE any real run.
- Run the acceptance-tooling SELF-CHECK (no CPN launch): exercise the pure
  ownership helpers and prove fail-closed identity:
  `is_local_run_worker` returns False for wrong/missing argv and for a wrong
  `run_id`; True only for exact `-m app.analysis.local_inference_worker <run_id>`;
  `is_dataset_experiment_worker` returns True only for exact
  `-m app.dataset_experiments.worker <experiment_id> --coordinator-token <token>`
  and False for a wrong token/experiment; `read_proc_argv` returns `None` for a
  missing PID. Example (run with the control-plane interpreter):
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" - <<'PY'
  import importlib.util, sys
  spec = importlib.util.spec_from_file_location("g6_driver", "scripts/g6_acceptance_driver.py")
  # Only import the helpers by exec of the function defs is unsafe (driver runs);
  # instead assert on a copy of the two pure helpers loaded from this plan's
  # verification snippet:
  from pathlib import Path
  def read_proc_argv(pid):
      try:
          raw = Path(f"/proc/{pid}/cmdline").read_bytes()
      except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
          return None
      if not raw:
          return None
      return [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]
  def is_local_run_worker(argv, run_id):
      if not argv:
          return False
      try:
          i = argv.index("-m")
      except ValueError:
          return False
      return len(argv) > i + 2 and argv[i+1] == "app.analysis.local_inference_worker" and argv[i+2] == run_id
  def is_dataset_experiment_worker(argv, eid, tok):
      if not argv:
          return False
      try:
          mi = argv.index("-m"); ti = argv.index("--coordinator-token")
      except ValueError:
          return False
      return (len(argv) > mi+2 and argv[mi+1] == "app.dataset_experiments.worker"
              and argv[mi+2] == eid and len(argv) > ti+1 and argv[ti+1] == tok)
  assert is_local_run_worker(None, "run_x") is False
  assert is_local_run_worker(["python", "app.analysis.local_inference_worker", "run_x"], "run_x") is False
  assert is_local_run_worker(["-m", "app.analysis.local_inference_worker", "run_x"], "run_x") is True
  assert is_local_run_worker(["-m", "app.analysis.local_inference_worker", "run_y"], "run_x") is False
  c = ["-m", "app.dataset_experiments.worker", "exp_1", "--coordinator-token", "tok_1"]
  assert is_dataset_experiment_worker(c, "exp_1", "tok_1") is True
  assert is_dataset_experiment_worker(c, "exp_1", "tok_2") is False
  assert is_dataset_experiment_worker(c, "exp_2", "tok_1") is False
  assert read_proc_argv(999999999) is None
  print("G6_OWNERSHIP_SELFCHECK_OK")
  PY
  ```
  NOTE: the self-check above re-declares the two pure helpers verbatim; the
  implementer may instead import them if the driver guards its main body behind
  `if __name__ == "__main__":`. Either way it MUST prove the five identity cases
  without launching CPN.
- Commit: `test: add G6 real acceptance driver` (script only).
- ONLY AFTER that commit is clean: execute the driver with the exact env above.
  Record experiment id, frozen identity, and the pre-run run-id snapshot.
- STOP on MEM-1/MEM-2/RUN-1.

### Task 5 — Execute Three Real Runs (real execution)

- Poll to terminal inference state; capture per-item/attempt/run evidence.
- Verify 3 new runs (proved disjoint from `pre_run_ids` by the driver assertion),
  all completed, DetectionResults persisted per run.
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
- Commit: `docs: add phase G G6 real acceptance evidence` (evidence doc only).

---

## Verification Commands (index)

```bash
# preflight
cd /root/autodl-tmp/WISA-m9-2-implementation
git branch --show-current; git rev-parse HEAD; git status --short
cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/memory.current
"$PWD/.venv/bin/python" --version
ls -la /root/miniconda3/bin/python

# asset hash fail-closed checks (STOP ASSET-1 on missing/mismatch)
"$PWD/.venv/bin/python" - <<'PY'
import hashlib
from pathlib import Path
expected = {
    "/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt":
        "eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311",
    "/root/autodl-tmp/release/support/normalization_ls_stft.json":
        "9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f",
}
for raw, want in expected.items():
    path = Path(raw)
    if not path.is_file():
        raise SystemExit(f"STOP ASSET-1: missing {path}")
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != want:
        raise SystemExit(f"STOP ASSET-1: {path} sha256 {got} != {want}")
print("ASSET_HASHES_OK")
PY

# focused G5 seal-hardening
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_coordinator_evaluating.py::test_post_link_failure_projection_stale_generation_returns_fence_lost -v

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
backend/tests/test_dataset_experiment_coordinator_evaluating.py   (Task 3, test-only)
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
15. Every G5 seal-hardening command points to
    `test_dataset_experiment_coordinator_evaluating.py`. PASS
16. The acceptance driver is ONE complete executable script; all acceptance
    assertions run BEFORE success evidence is written. PASS
17. Historical-run isolation is an executable assertion
    (`set(final_run_ids) ∩ set(pre_run_ids) == empty`). PASS
18. MEM-2 is implemented (5 s sampling, peak tracking, threshold), with
    acceptance-owned PID cleanup on MEM-2 and on timeout; no unrelated process
    killed and no DB state mutated. PASS
19. Registration and driver scripts each have an explicit commit owner
    (Task 2 and Task 4 respectively, committed before execution). PASS
20. Asset hashes are compared fail-closed; base SHA is unambiguous
    (`3d9f47b...`); execution-time recording/manifest evidence is authoritative. PASS
21. Cleanup candidates never come from terminal Experiment/Run state (active
    `{running,evaluating}` / `{pending,running}` only). PASS
22. PID reuse cannot satisfy ownership without exact parsed-argv identity. PASS
23. `/proc` missing/unreadable/permission-denied → skip (fail closed). PASS
24. Coordinator identity includes the exact experiment id AND coordinator token. PASS
25. Inference identity includes the exact AnalysisRun id and worker module. PASS
26. Cleanup stops the validated coordinator before validated inference workers. PASS
27. MEM-2 and timeout use the same fenced cleanup; no DB state mutation. PASS
28. Task 4 requires an executable ownership self-check (no CPN launch) before the
    driver is committed/run. PASS
29. All prior G6 corrections remain intact (stems 0/1/2, registration, seal
    hardening, MEM-1/MEM-2 thresholds, historical-run assertions, CPN/golden
    identity, local_cpu/cpu/float32, max_concurrency=1, interpreter, asset SHA
    checks, protocol, evaluation/classification assertions, evidence schema,
    commit ownership, no-production-change rule). PASS
30. Only the G6 plan document changes in this correction. PASS

---

## Verification

- `git diff --check` clean after the plan-only commit.
- Only the G6 plan document changes in this pass.
- Placeholder scan NONE.
- Real execution remains gated by MEM-1 at run time.

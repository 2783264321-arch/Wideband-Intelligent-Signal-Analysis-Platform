# M8.6C-2 Real 2500-Sample Benchmark Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create exactly one formal `physical_tf_detection_ap_v2` DatasetEvaluation over the already imported 2500 SpaceNet test runs, verify the frozen 2500/33373/20018→19962 invariants, prove deterministic recomputation, and complete a historical parity audit.

**Architecture:** C-2 adds no SpaceNet-specific product evaluator and performs no model inference. It uses the C-1 imported-batch resolver to freeze exact ordinary AnalysisRuns, runs the normal benchmark worker once, then independently recomputes metrics from the same frozen database inputs without creating a second DatasetEvaluation. Historical ZoomSpec values live only in the research acceptance note/acceptance commands.

**Tech Stack:** Existing Python backend, SQLAlchemy/SQLite, benchmark worker/evaluation modules, pytest for preflight regression, Markdown research evidence.

**Spec:** `docs/superpowers/specs/2026-09-06-m8-6c-dataset-benchmark-ui-real-evaluation-design.md`

## Global Constraints

- Local execution target is Windows. Shell snippets in this plan use POSIX/Bash syntax for heredocs and environment variables; 本地电脑opencode must run those blocks from Git Bash at the repository root (or translate them exactly to PowerShell without changing semantics).
- C-1 must already be accepted; `docs/research/m8_6c_protocol_membership_foundation.md` must report a green backend suite and exact 2500-run resolver coverage.
- Real batch fingerprint is exactly `c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5`.
- Real manifest hash is exactly `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`.
- Dataset is platform `SpaceNet / test`, label space `spacenet_14`, pipeline `zoomspec_yolo26n_aug_combined_frn_v3 / 1.0.0`.
- Formal input invariants: 2500 recordings, 33373 predictions, raw GT 20018, v2 canonical GT 19962, duplicates removed 56, full coverage.
- No YOLO/FRN/AHLP inference, NMS recomputation, retraining, DSP, GPU work, or rewriting imported detections.
- Create only one formal real DatasetEvaluation. Do not create a second duplicate evaluation to prove determinism.
- Historical reference values are research-only: mAP50 `0.49706861157413673`, mAP50:95 `0.37325127587379914`.
- C-2 cannot pass with `PARITY DIFFERENCE UNEXPLAINED`.
- If a metric mismatch is unexplained, stop before code changes and use the systematic-debugging workflow; do not tune evaluator behavior toward the old scalar.
- A parity audit must not redefine the already approved v2 protocol. If implementation violates the approved v2 definition, fix that bug under systematic debugging and rerun the full C-1 gate. If historical code simply uses a different evaluator definition, document the difference instead of changing v2.

---

### Task 1: Preflight and create exactly one formal real DatasetEvaluation

**Files:**
- No product code.
- Read: `docs/research/m8_6c_protocol_membership_foundation.md`
- Later create: `docs/research/m8_6c_real_dataset_benchmark.md`

**Interfaces:**
- Consumes: `DatasetBenchmarkService.resolve_imported_batch()` and v2-default `create_evaluation()` from C-1.
- Produces: one pending formal DatasetEvaluation id stored in shell variable `M86C_EVAL_ID`.

- [ ] **Step 1: Confirm clean accepted C-1 state**

```bash
git status --short
git log -1 --oneline
pytest -q
```

Expected: clean tree and full backend suite PASS. Stop on any regression.

- [ ] **Step 2: Prove the process is pointed at the real local platform database**

Run from `backend/`:

```bash
python - <<'PY'
from app.core.config import Settings

settings = Settings()
normalized = str(settings.database_url).replace("\\", "/")
expected_suffix = "D:/LGFiles/Wideband Signal Analysis Platform/Wideband-Intelligent-Signal-Analysis-Platform/platform.db"
print("project_root=", settings.project_root)
print("database_url=", settings.database_url)
assert normalized.startswith("sqlite:///")
assert normalized.endswith(expected_suffix), normalized
PY
```

Required: the printed DB is the known real `platform.db` under `D:\LGFiles\Wideband Signal Analysis Platform\Wideband-Intelligent-Signal-Analysis-Platform`. Stop if an environment variable points `WSP_DATABASE_URL` elsewhere. Never run C-2 against a temp/test DB.

- [ ] **Step 3: Atomically resolve and create one evaluation, refusing duplicates by name**

Run from `backend/` in one shell session:

```bash
export M86C_EVAL_ID="$(python - <<'PY'
from sqlalchemy import select

from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.core.config import Settings
from app.db.session import Database

FP = "c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5"
NAME = "M8.6C SpaceNet Test - ZoomSpec YOLO26n Aug + Combined FRN V3"
EXPECTED_MANIFEST = "91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b"

settings = Settings()
db = Database(settings.database_url)
with db.session_factory() as session:
    existing = list(session.scalars(
        select(DatasetEvaluationModel).where(DatasetEvaluationModel.name == NAME)
    ).all())
    if existing:
        raise SystemExit(f"Refusing duplicate formal evaluation: {[e.id for e in existing]}")

    svc = DatasetBenchmarkService(session)
    resolved = svc.resolve_imported_batch(FP)
    assert resolved.recording_manifest_hash == EXPECTED_MANIFEST
    assert resolved.expected_recordings == 2500
    assert resolved.resolved_recordings == 2500
    assert resolved.missing_recordings == 0
    assert resolved.conflict_count == 0
    assert len({entry.analysis_run_id for entry in resolved.entries}) == 2500

    evaluation = svc.create_evaluation(
        name=NAME,
        dataset_name=resolved.dataset_name,
        dataset_split=resolved.dataset_split,
        label_space=resolved.label_space,
        recording_manifest_hash=resolved.recording_manifest_hash,
        items=[
            {"recording_id": entry.recording_id, "analysis_run_id": entry.analysis_run_id}
            for entry in resolved.entries
        ],
    )
    assert evaluation.evaluation_protocol == "physical_tf_detection_ap_v2"
    assert evaluation.expected_recordings == 2500
    assert evaluation.evaluated_recordings == 2500
    assert evaluation.missing_recordings == 0
    assert evaluation.coverage == 1.0
    print(evaluation.id)
PY
)"
printf 'M86C_EVAL_ID=%s\n' "$M86C_EVAL_ID"
```

Required: one id beginning with `eval_`; no worker started yet.

- [ ] **Step 4: Verify membership is the exact semantic batch before running**

```bash
python - <<'PY'
import os
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationModel
from app.core.config import Settings
from app.db.session import Database

FP = "c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5"
eval_id = os.environ["M86C_EVAL_ID"]
db = Database(Settings().database_url)
with db.session_factory() as session:
    evaluation = session.scalar(
        select(DatasetEvaluationModel)
        .where(DatasetEvaluationModel.id == eval_id)
        .options(selectinload(DatasetEvaluationModel.items))
    )
    run_ids = [item.analysis_run_id for item in evaluation.items]
    runs = list(session.scalars(select(AnalysisRunModel).where(AnalysisRunModel.id.in_(run_ids))).all())
    assert len(runs) == 2500
    assert len(set(run_ids)) == 2500
    assert all(((run.parameters_json or {}).get("batch_import") or {}).get("import_fingerprint") == FP for run in runs)
    print("membership=PASS", len(runs))
PY
```

Expected: `membership=PASS 2500`.

- [ ] **Step 5: Commit nothing yet**

The DatasetEvaluation is real mutable local data, not a source change. Do not create a docs commit before the worker result exists.

---

### Task 2: Run the normal worker once and verify all frozen input/result invariants

**Files:**
- No product code.

**Interfaces:**
- Consumes: `M86C_EVAL_ID` from Task 1 and normal `app.benchmarks.worker`.
- Produces: one completed formal real DatasetEvaluation.

- [ ] **Step 1: Run the normal worker directly**

From `backend/`:

```bash
python -m app.benchmarks.worker "$M86C_EVAL_ID"
```

Expected: process exits `0`. Do not interrupt unless it has clearly failed; do not start a second worker for the same evaluation.

- [ ] **Step 2: Verify status, coverage, GT accounting, prediction count, and classification capability**

```bash
python - <<'PY'
import os
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationModel
from app.core.config import Settings
from app.db.session import Database
from app.detections.model import DetectionResultModel
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

EXPECTED = {
    "manifest": "91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b",
    "predictions": 33373,
    "raw_gt": 20018,
    "canonical_gt": 19962,
    "removed": 56,
}

eval_id = os.environ["M86C_EVAL_ID"]
db = Database(Settings().database_url)
with db.session_factory() as session:
    evaluation = session.scalar(
        select(DatasetEvaluationModel)
        .where(DatasetEvaluationModel.id == eval_id)
        .options(selectinload(DatasetEvaluationModel.items))
    )
    assert evaluation.status == "completed"
    assert evaluation.expected_recordings == 2500
    assert evaluation.evaluated_recordings == 2500
    assert evaluation.missing_recordings == 0
    assert evaluation.coverage == 1.0
    assert evaluation.comparable is True
    assert evaluation.evaluation_protocol == "physical_tf_detection_ap_v2"
    assert evaluation.recording_manifest_hash == EXPECTED["manifest"]

    aggregate = evaluation.aggregate_metrics_json
    assert aggregate["ground_truth"]["raw_count"] == EXPECTED["raw_gt"]
    assert aggregate["ground_truth"]["canonical_count"] == EXPECTED["canonical_gt"]
    assert aggregate["ground_truth"]["duplicates_removed"] == EXPECTED["removed"]
    assert aggregate["ground_truth"]["duplicate_policy"] == "exact_physical_class_dedup"
    assert aggregate["classification_applicable"] is True
    assert aggregate["class_aware"] is not None

    prediction_count = sum(item.prediction_count for item in evaluation.items)
    canonical_item_gt = sum(item.gt_count for item in evaluation.items)
    assert prediction_count == EXPECTED["predictions"]
    assert canonical_item_gt == EXPECTED["canonical_gt"]

    raw_gt = session.scalar(
        select(func.count(GroundTruthModel.id))
        .join(RecordingModel, GroundTruthModel.recording_id == RecordingModel.id)
        .where(
            RecordingModel.dataset_name == "SpaceNet",
            RecordingModel.dataset_split == "test",
            RecordingModel.label_space == "spacenet_14",
        )
    )
    assert raw_gt == EXPECTED["raw_gt"]

    print("status", evaluation.status)
    print("predictions", prediction_count)
    print("raw_gt", raw_gt)
    print("canonical_gt", canonical_item_gt)
    print("localization_ap50", aggregate["localization"]["ap50"])
    print("localization_ap50_95", aggregate["localization"]["ap50_95"])
    print("class_aware_map50", aggregate["class_aware"]["map50"])
    print("class_aware_map50_95", aggregate["class_aware"]["map50_95"])
    print("matched_accuracy", aggregate["classification_on_matched"]["matched_accuracy"])
    print("per_class_rows", len(evaluation.per_class_metrics_json or []))
    print("confusion_rows", len(evaluation.confusion_json or []))
PY
```

Required invariant outputs: 33373, 20018, 19962, and 14 per-class rows.

- [ ] **Step 3: Stop if any input invariant failed**

Do not perform parity analysis when membership/GT/prediction counts differ. Treat that as a C-1/C-2 input defect and debug before interpreting metrics.

---

### Task 3: Independently recompute the frozen metric payload without inserting another evaluation

**Files:**
- No product code.

**Interfaces:**
- Consumes: the completed evaluation membership and C-1 `build_protocol_view()`.
- Produces: independent metric values that equal the persisted formal result.

- [ ] **Step 1: Run a pure recomputation over the same frozen inputs**

```bash
python - <<'PY'
import math
import os
from sqlalchemy import select

from app.benchmarks.loader import BenchmarkInputLoader
from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.protocol import build_protocol_view
from app.core.config import Settings
from app.db.session import Database
from app.evaluation.ap import class_aware_ap_summary, localization_ap_summary
from app.evaluation.capability import classification_applicability
from app.evaluation.dataset_metrics import compute_dataset_diagnostics
from app.pipelines.registry import create_pipeline_registry


def close(a, b):
    if a is None or b is None:
        assert a is b
    else:
        assert math.isclose(a, b, rel_tol=0.0, abs_tol=1e-15), (a, b)


eval_id = os.environ["M86C_EVAL_ID"]
db = Database(Settings().database_url)
registry = create_pipeline_registry()
with db.session_factory() as session:
    evaluation = session.get(DatasetEvaluationModel, eval_id)
    raw = BenchmarkInputLoader(session).load(eval_id)
    view = build_protocol_view(evaluation.evaluation_protocol, raw)

    applicability = None
    reason = None
    for recording_id, run in view.runs_by_recording.items():
        result = classification_applicability(run, view.recordings_by_id[recording_id], registry)
        current = (result.applicable, result.reason)
        if applicability is None:
            applicability, reason = current
        else:
            assert current == (applicability, reason)
    assert applicability is True

    diagnostics = compute_dataset_diagnostics(list(view.samples), classification_applicable=True)
    loc = localization_ap_summary(list(view.ground_truths), list(view.predictions))
    cls = class_aware_ap_summary(list(view.ground_truths), list(view.predictions))
    persisted = evaluation.aggregate_metrics_json

    assert view.ground_truth_accounting.raw_count == 20018
    assert view.ground_truth_accounting.canonical_count == 19962
    assert view.ground_truth_accounting.removed_count == 56
    assert len(view.predictions) == 33373

    close(loc.ap50, persisted["localization"]["ap50"])
    close(loc.ap50_95, persisted["localization"]["ap50_95"])
    assert diagnostics.localization.tp == persisted["localization"]["operating"]["tp"]
    assert diagnostics.localization.fp == persisted["localization"]["operating"]["fp"]
    assert diagnostics.localization.fn == persisted["localization"]["operating"]["fn"]
    close(diagnostics.localization.precision, persisted["localization"]["operating"]["precision"])
    close(diagnostics.localization.recall, persisted["localization"]["operating"]["recall"])
    close(diagnostics.localization.f1, persisted["localization"]["operating"]["f1"])

    close(cls.map50, persisted["class_aware"]["map50"])
    close(cls.map50_95, persisted["class_aware"]["map50_95"])
    assert diagnostics.classification.matched_count == persisted["classification_on_matched"]["matched_count"]
    assert diagnostics.classification.class_correct == persisted["classification_on_matched"]["class_correct"]
    assert diagnostics.classification.class_wrong == persisted["classification_on_matched"]["class_wrong"]
    close(diagnostics.classification.matched_accuracy, persisted["classification_on_matched"]["matched_accuracy"])
    assert diagnostics.class_aware.tp == persisted["class_aware"]["operating"]["tp"]
    assert diagnostics.class_aware.fp == persisted["class_aware"]["operating"]["fp"]
    assert diagnostics.class_aware.fn == persisted["class_aware"]["operating"]["fn"]
    close(diagnostics.class_aware.precision, persisted["class_aware"]["operating"]["precision"])
    close(diagnostics.class_aware.recall, persisted["class_aware"]["operating"]["recall"])
    close(diagnostics.class_aware.f1, persisted["class_aware"]["operating"]["f1"])

    persisted_per_class = {row["class_id"]: row for row in evaluation.per_class_metrics_json}
    diagnostics_per_class = {row.class_id: row for row in diagnostics.per_class}
    assert len(cls.per_class) == len(persisted_per_class) == 14
    for item in cls.per_class:
        row = persisted_per_class[item.class_id]
        assert item.gt_count == row["gt_count"]
        assert item.prediction_count == row["prediction_count"]
        close(item.ap50, row["ap50"])
        close(item.ap50_95, row["ap50_95"])
        operating = diagnostics_per_class[item.class_id]
        assert operating.tp == row["operating"]["tp"]
        assert operating.fp == row["operating"]["fp"]
        assert operating.fn == row["operating"]["fn"]
        close(operating.precision, row["operating"]["precision"])
        close(operating.recall, row["operating"]["recall"])
        close(operating.f1, row["operating"]["f1"])

    persisted_conf = {
        (row["gt_class_id"], row["pred_class_id"]): row["count"]
        for row in evaluation.confusion_json
    }
    recomputed_conf = {
        (row.gt_class_id, row.pred_class_id): row.count
        for row in diagnostics.classification.confusions
    }
    assert persisted_conf == recomputed_conf
    print("deterministic_recompute=PASS")
PY
```

Expected: `deterministic_recompute=PASS`.

- [ ] **Step 2: Do not call the worker again**

The completed evaluation remains immutable; the independent recomputation above is the determinism evidence.

---

### Task 4: Run historical parity audit and classify the result

**Files:**
- Create: `docs/research/m8_6c_real_dataset_benchmark.md`

**Interfaces:**
- Consumes: persisted platform class-aware mAP50/mAP50:95 and frozen historical values.
- Produces: `PARITY CONFIRMED`, `PARITY DIFFERENCE EXPLAINED`, or a blocking `PARITY DIFFERENCE UNEXPLAINED` result.

- [ ] **Step 1: Compute exact deltas with 1e-12 classification tolerance**

```bash
python - <<'PY'
import os
from app.benchmarks.model import DatasetEvaluationModel
from app.core.config import Settings
from app.db.session import Database

HIST_MAP50 = 0.49706861157413673
HIST_MAP50_95 = 0.37325127587379914
TOL = 1e-12

db = Database(Settings().database_url)
with db.session_factory() as session:
    evaluation = session.get(DatasetEvaluationModel, os.environ["M86C_EVAL_ID"])
    current = evaluation.aggregate_metrics_json["class_aware"]
    platform_map50 = current["map50"]
    platform_map50_95 = current["map50_95"]
    delta50 = platform_map50 - HIST_MAP50
    delta5095 = platform_map50_95 - HIST_MAP50_95
    confirmed = abs(delta50) <= TOL and abs(delta5095) <= TOL
    print("platform_map50", repr(platform_map50))
    print("historical_map50", repr(HIST_MAP50))
    print("delta_map50", repr(delta50))
    print("platform_map50_95", repr(platform_map50_95))
    print("historical_map50_95", repr(HIST_MAP50_95))
    print("delta_map50_95", repr(delta5095))
    print("parity", "PARITY CONFIRMED" if confirmed else "PARITY AUDIT REQUIRED")
PY
```

- [ ] **Step 2A: If parity is confirmed, record that exact outcome**

Write the research note with exact platform values, historical values, deltas, `abs_tol=1e-12`, and conclusion `PARITY CONFIRMED`.

- [ ] **Step 2B: If parity is not confirmed, stop implementation changes and audit in the fixed order**

Do not change product code yet. Check in order:

```text
1. frozen Recording/Run membership
2. exact canonical 19962 GT identity
3. exact 33373 prediction identity, boxes, classes, confidence
4. confidence ranking and tie-breaks
5. physical TF IoU
6. greedy matching and equal-IoU behavior
7. 101-point AP integration
8. per-class macro averaging / zero-GT handling
```

Because this is an unexpected-result investigation, use the project systematic-debugging workflow before proposing any fix. If the audit reveals that the platform implementation violates the already approved v2 definition, C-2 immediately FAILS: do not delete, mutate, or silently replace the completed formal evaluation and do not create a second formal real evaluation. Fix the source under the C-1 gate, report the now-stale acceptance evaluation id, and ask for an explicit data-disposition decision before attempting C-2 again.

- [ ] **Step 3: Use 服务器opencode only if local frozen inputs agree but evaluator semantics remain unexplained**

The read-only server audit must inspect the historical evaluator source rooted in the known legacy trees, especially the `evaluate_detections.py` called by `/root/autodl-tmp/Claude/scripts/run_test_new_pipeline.py`.

The audit must report, without modifying files or re-running inference:

```text
exact GT duplicate key
prediction sort order and score tie behavior
physical IoU formula
matching rule and equal-IoU tie behavior
AP interpolation/integration
class averaging / zero-GT policy
```

If those semantics concretely explain the delta, record `PARITY DIFFERENCE EXPLAINED`. If not, classify `PARITY DIFFERENCE UNEXPLAINED` and fail C-2.

- [ ] **Step 4: Write the complete acceptance note**

`docs/research/m8_6c_real_dataset_benchmark.md` must contain exact observed values for:

```text
platform commit SHA
DatasetEvaluation id
batch fingerprint
recording manifest hash
protocol name + protocol_config_json
expected/evaluated/missing/coverage
prediction count
raw/canonical/removed GT counts
localization AP50 and AP50:95
localization operating TP/FP/FN/P/R/F1
matched count/correct/wrong/accuracy
class-aware mAP50 and mAP50:95
class-aware operating TP/FP/FN/P/R/F1
14 per-class rows or a compact table copied from persisted JSON
confusion row count and top confusions
historical mAP50/mAP50:95
both deltas
parity conclusion
whether 服务器opencode audit was needed
```

Do not claim a historical evaluator detail unless it was actually inspected or already frozen in the existing provenance docs.

- [ ] **Step 5: Commit the acceptance note only after the parity conclusion is resolved**

```bash
git add docs/research/m8_6c_real_dataset_benchmark.md
git commit -m "docs: record m8.6c real dataset benchmark"
```

---

### Task 5: C-2 final gate and handoff to UI

**Files:**
- No additional files expected.

- [ ] **Step 1: Re-run backend regression after any parity-related source change**

If parity was confirmed with no source changes, the C-1 full-suite result plus real acceptance is sufficient; nevertheless run:

```bash
pytest -q
```

If any source fix was made after a systematic-debugging audit, this full suite is mandatory and the fix must have its own focused regression test before the full run.

- [ ] **Step 2: Verify one-and-only-one formal real evaluation remains**

```bash
python - <<'PY'
from sqlalchemy import select
from app.benchmarks.model import DatasetEvaluationModel
from app.core.config import Settings
from app.db.session import Database

NAME = "M8.6C SpaceNet Test - ZoomSpec YOLO26n Aug + Combined FRN V3"
db = Database(Settings().database_url)
with db.session_factory() as session:
    rows = list(session.scalars(select(DatasetEvaluationModel).where(DatasetEvaluationModel.name == NAME)).all())
    assert len(rows) == 1, [row.id for row in rows]
    assert rows[0].status == "completed"
    print(rows[0].id)
PY
```

- [ ] **Step 3: Gate decision**

C-2 is PASS only when:

```text
formal evaluation completed
2500/2500 coverage
33373 predictions
raw GT 20018
canonical GT 19962
removed 56
classification applicable
aggregate/per-class/confusion complete
independent deterministic recomputation PASS
historical parity == PARITY CONFIRMED or PARITY DIFFERENCE EXPLAINED
exactly one formal real evaluation exists
research acceptance note committed
working tree clean
```

Stop and report the exact platform mAP values and parity conclusion. Do not begin C-3 until this gate is accepted.

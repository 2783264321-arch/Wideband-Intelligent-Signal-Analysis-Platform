# M8.6C-1 Protocol & Membership Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `physical_tf_detection_ap_v2`, exact per-Recording Ground-Truth deduplication, protocol-aware benchmark execution/counting, and exact imported-batch catalog/resolution without changing raw data or v1 semantics.

**Architecture:** Keep `BenchmarkInputLoader` raw. Add a focused benchmark protocol module that maps a loaded raw benchmark to a protocol-specific evaluation view. DatasetEvaluation creation defaults to v2 and computes protocol-specific item GT counts from the frozen manifest. Imported batches remain a derived read model over ordinary completed imported AnalysisRuns; resolution by semantic batch fingerprint returns an exact frozen Recording→AnalysisRun mapping and never selects runs by recency.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Pydantic, pytest, SQLite.

**Spec:** `docs/superpowers/specs/2026-09-06-m8-6c-dataset-benchmark-ui-real-evaluation-design.md`

## Global Constraints

- Local execution target is Windows. Shell snippets in this plan use POSIX/Bash syntax for heredocs and environment variables; 本地电脑opencode must run those blocks from Git Bash at the repository root (or translate them exactly to PowerShell without changing semantics).
- Baseline before implementation: `feature/v1-core @ 1036e51b70a351dfcae0330c8e5e725844640bc8`.
- `physical_tf_detection_ap_v1` keeps raw-GT semantics; do not reinterpret or rewrite old v1 rows/config.
- `physical_tf_detection_ap_v2` differs from v1 only by `gt_duplicate_policy=exact_physical_class_dedup` and `ground_truth_view=evaluation_canonical`.
- Exact duplicate key is per Recording: `.17g` canonicalized `t_start_s`, `t_end_s`, `f_low_hz`, `f_high_hz`, plus exact `class_id`; no epsilon, IoU, tolerance, or `class_name` identity.
- Raw GroundTruth rows and the raw `recording_manifest_hash` are never mutated.
- One v2 canonical GT set feeds localization AP, class-aware AP, operating diagnostics, matched classification, and confusion aggregation.
- No new persistence table, `BatchRun`, `BatchImportModel`, or batch foreign key.
- Imported batch membership is selected by exact `parameters_json.batch_import.import_fingerprint`; never auto-select newest/oldest run.
- C-1 must pass before the formal 2500-sample DatasetEvaluation is started.
- Use TDD: red test → minimal implementation → focused green → commit for every task.

---

## File Structure

**Create**
- `backend/app/benchmarks/protocol.py` — protocol definitions, v1/v2 lookup, exact GT canonicalization, and construction of protocol-specific benchmark views.
- `backend/tests/test_benchmark_protocol.py` — pure protocol/canonicalization behavior.
- `backend/tests/test_benchmark_imported_batch.py` — imported-batch catalog/resolver service behavior.

**Modify**
- `backend/app/benchmarks/schema.py` — explicit v1/v2 constants/config plus imported-batch API schemas.
- `backend/app/benchmarks/service.py` — default v2 creation, protocol-specific item counts, imported-batch catalog/resolver.
- `backend/app/benchmarks/worker.py` — dispatch loaded inputs through the frozen evaluation protocol before every metric.
- `backend/app/benchmarks/router.py` — catalog and imported-batch resolve endpoints.
- `backend/tests/benchmark_fixture.py` — allow explicit `parameters_json` when building AnalysisRun fixtures.
- `backend/tests/test_benchmark_membership.py` — v2 default/item-count behavior and raw manifest identity.
- `backend/tests/test_benchmark_worker.py` — v2 canonical metrics, v1 compatibility, GT provenance.
- `backend/tests/test_benchmark_api.py` — new API contracts and default v2 creation.

No migration file should change in C-1.

---

### Task 1: Define v1/v2 protocols and exact canonical GT view

**Files:**
- Create: `backend/app/benchmarks/protocol.py`
- Create: `backend/tests/test_benchmark_protocol.py`
- Modify: `backend/app/benchmarks/schema.py`

**Interfaces:**
- Consumes: `BenchmarkInputLoader` raw `LoadedBenchmark`, `EvaluationGroundTruth`, `EvaluationSample`, and `canonical_number()` from `app.benchmarks.manifest`.
- Produces:
  - `PHYSICAL_TF_PROTOCOL_V1: str`
  - `PHYSICAL_TF_PROTOCOL_V2: str`
  - `DEFAULT_PHYSICAL_TF_PROTOCOL: str`
  - `PROTOCOL_CONFIG_V1: dict`
  - `PROTOCOL_CONFIG_V2: dict`
  - `GroundTruthAccounting(raw_count: int, canonical_count: int, removed_count: int)`
  - `ProtocolBenchmarkView(samples, ground_truths, predictions, runs_by_recording, recordings_by_id, ground_truth_accounting)`
  - `canonicalize_ground_truths(ground_truths)`
  - `build_protocol_view(evaluation_protocol, loaded)`
  - `count_ground_truths_for_protocol(evaluation_protocol, ground_truths)`

- [ ] **Step 1: Write failing canonicalization and protocol lookup tests**

Create `backend/tests/test_benchmark_protocol.py` with focused tests like:

```python
from dataclasses import replace

import pytest

from app.benchmarks.protocol import (
    PHYSICAL_TF_PROTOCOL_V1,
    PHYSICAL_TF_PROTOCOL_V2,
    build_protocol_view,
    canonicalize_ground_truths,
)
from app.evaluation.ap import EvaluationGroundTruth, EvaluationPrediction
from app.evaluation.dataset_metrics import EvaluationSample
from app.benchmarks.loader import LoadedBenchmark


def gt(*, recording_id="rec_a", order=0, t0=0.01, t1=0.02,
       f0=2_440_600_000.0, f1=2_440_700_000.0, class_id=9,
       class_name="LoRa 250kHz"):
    return EvaluationGroundTruth(
        recording_id=recording_id,
        manifest_order=order,
        t_start_s=t0,
        t_end_s=t1,
        f_low_hz=f0,
        f_high_hz=f1,
        class_id=class_id,
        class_name=class_name,
    )


def loaded(*gts):
    by_recording = {}
    for item in gts:
        by_recording.setdefault((item.recording_id, item.manifest_order), []).append(item)
    samples = tuple(
        EvaluationSample(
            recording_id=recording_id,
            manifest_order=order,
            ground_truths=tuple(items),
            predictions=(),
        )
        for (recording_id, order), items in sorted(by_recording.items(), key=lambda kv: kv[0][1])
    )
    return LoadedBenchmark(
        samples=samples,
        ground_truths=tuple(gts),
        predictions=(),
        runs_by_recording={},
        recordings_by_id={},
    )


def test_v2_removes_exact_physical_class_duplicate():
    a = gt()
    duplicate = replace(a, class_name="display alias does not affect identity")
    view = build_protocol_view(PHYSICAL_TF_PROTOCOL_V2, loaded(a, duplicate))
    assert len(view.ground_truths) == 1
    assert view.ground_truth_accounting.raw_count == 2
    assert view.ground_truth_accounting.canonical_count == 1
    assert view.ground_truth_accounting.removed_count == 1


def test_v1_keeps_exact_duplicate():
    a = gt()
    view = build_protocol_view(PHYSICAL_TF_PROTOCOL_V1, loaded(a, a))
    assert len(view.ground_truths) == 2
    assert view.ground_truth_accounting.raw_count == 2
    assert view.ground_truth_accounting.canonical_count == 2
    assert view.ground_truth_accounting.removed_count == 0


def test_minuscule_coordinate_change_is_not_duplicate():
    a = gt()
    b = replace(a, t_end_s=a.t_end_s + 1e-15)
    assert len(canonicalize_ground_truths((a, b))) == 2


def test_same_box_different_class_is_not_duplicate():
    a = gt()
    b = replace(a, class_id=8, class_name="Zigbee")
    assert len(canonicalize_ground_truths((a, b))) == 2


def test_same_box_different_recording_is_not_duplicate():
    a = gt(recording_id="rec_a", order=0)
    b = gt(recording_id="rec_b", order=1)
    view = build_protocol_view(PHYSICAL_TF_PROTOCOL_V2, loaded(a, b))
    assert len(view.ground_truths) == 2


def test_canonical_output_order_ignores_input_order_and_uses_class_name_only_as_display_tie_break():
    early = gt(t0=0.01, class_name="aaa")
    late = gt(t0=0.03, class_name="zzz")
    duplicate_a = gt(t0=0.05, class_name="zzz")
    duplicate_b = gt(t0=0.05, class_name="aaa")
    first = canonicalize_ground_truths((late, duplicate_a, early, duplicate_b))
    second = canonicalize_ground_truths((duplicate_b, early, duplicate_a, late))
    assert [(x.t_start_s, x.class_id) for x in first] == [(0.01, 9), (0.03, 9), (0.05, 9)]
    assert first == second
    assert first[-1].class_name == "aaa"


def test_unknown_protocol_is_rejected():
    with pytest.raises(ValueError, match="Unsupported evaluation protocol"):
        build_protocol_view("physical_tf_detection_ap_v999", loaded(gt()))
```

Use `GroundTruthAccounting` field assertions rather than tuple equality if implemented as a dataclass.

- [ ] **Step 2: Run the new test module and confirm RED**

Run from `backend/`:

```bash
pytest tests/test_benchmark_protocol.py -q
```

Expected: collection/import failure because `app.benchmarks.protocol` and v2 constants do not exist.

- [ ] **Step 3: Replace the single protocol constant with explicit v1/v2 config**

In `backend/app/benchmarks/schema.py`, define:

```python
PHYSICAL_TF_PROTOCOL_V1 = "physical_tf_detection_ap_v1"
PHYSICAL_TF_PROTOCOL_V2 = "physical_tf_detection_ap_v2"
DEFAULT_PHYSICAL_TF_PROTOCOL = PHYSICAL_TF_PROTOCOL_V2

IOU_THRESHOLDS_V1 = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

_BASE_PROTOCOL_CONFIG = {
    "iou_thresholds": IOU_THRESHOLDS_V1,
    "ap_interpolation": "101_point_max_precision",
    "ap_recall_points": 101,
    "confidence_field": "DetectionResult.confidence",
    "diagnostic_matching": "hungarian_class_agnostic",
    "diagnostic_iou_threshold": 0.5,
    "ranking_tie_break": [
        "confidence_desc",
        "manifest_order",
        "t_start_s",
        "f_low_hz",
        "t_end_s",
        "f_high_hz",
        "class_id",
    ],
}

PROTOCOL_CONFIG_V1 = {
    **_BASE_PROTOCOL_CONFIG,
    "gt_duplicate_policy": "keep_all",
    "ground_truth_view": "raw",
}

PROTOCOL_CONFIG_V2 = {
    **_BASE_PROTOCOL_CONFIG,
    "gt_duplicate_policy": "exact_physical_class_dedup",
    "ground_truth_view": "evaluation_canonical",
}

# Compatibility alias for old imports only; it must continue to mean v1.
PHYSICAL_TF_PROTOCOL = PHYSICAL_TF_PROTOCOL_V1
```

Do not mutate a shared nested list after construction.

- [ ] **Step 4: Implement protocol-specific GT view**

Create `backend/app/benchmarks/protocol.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, TypeVar

from app.benchmarks.loader import LoadedBenchmark
from app.benchmarks.manifest import canonical_number
from app.benchmarks.schema import PHYSICAL_TF_PROTOCOL_V1, PHYSICAL_TF_PROTOCOL_V2
from app.evaluation.ap import EvaluationGroundTruth, EvaluationPrediction
from app.evaluation.dataset_metrics import EvaluationSample

GT = TypeVar("GT")


@dataclass(frozen=True)
class GroundTruthAccounting:
    raw_count: int
    canonical_count: int
    removed_count: int


@dataclass(frozen=True)
class ProtocolBenchmarkView:
    samples: tuple[EvaluationSample, ...]
    ground_truths: tuple[EvaluationGroundTruth, ...]
    predictions: tuple[EvaluationPrediction, ...]
    runs_by_recording: dict
    recordings_by_id: dict
    ground_truth_accounting: GroundTruthAccounting


def _semantic_key(gt) -> tuple[str, str, str, str, int]:
    return (
        canonical_number(gt.t_start_s),
        canonical_number(gt.t_end_s),
        canonical_number(gt.f_low_hz),
        canonical_number(gt.f_high_hz),
        int(gt.class_id),
    )


def _representative_sort_key(gt):
    # class_name is NOT part of duplicate identity. It is only the final deterministic
    # tie-break for choosing the display representative when duplicate rows disagree
    # on class_name. Local IDs / insertion order never participate.
    return (
        int(getattr(gt, "manifest_order", 0)),
        *_semantic_key(gt),
        str(gt.class_name),
    )


def canonicalize_ground_truths(ground_truths: Sequence[GT]) -> tuple[GT, ...]:
    # Called for one Recording at a time: recording_id is intentionally not part of the semantic key.
    by_key: dict[tuple[str, str, str, str, int], GT] = {}
    for gt in sorted(ground_truths, key=_representative_sort_key):
        by_key.setdefault(_semantic_key(gt), gt)
    return tuple(sorted(by_key.values(), key=_representative_sort_key))


def count_ground_truths_for_protocol(evaluation_protocol: str, ground_truths: Sequence[GT]) -> int:
    if evaluation_protocol == PHYSICAL_TF_PROTOCOL_V1:
        return len(ground_truths)
    if evaluation_protocol == PHYSICAL_TF_PROTOCOL_V2:
        return len(canonicalize_ground_truths(ground_truths))
    raise ValueError(f"Unsupported evaluation protocol: {evaluation_protocol}")


def build_protocol_view(evaluation_protocol: str, loaded: LoadedBenchmark) -> ProtocolBenchmarkView:
    if evaluation_protocol not in {PHYSICAL_TF_PROTOCOL_V1, PHYSICAL_TF_PROTOCOL_V2}:
        raise ValueError(f"Unsupported evaluation protocol: {evaluation_protocol}")

    if evaluation_protocol == PHYSICAL_TF_PROTOCOL_V1:
        samples = loaded.samples
    else:
        samples = tuple(
            EvaluationSample(
                recording_id=sample.recording_id,
                manifest_order=sample.manifest_order,
                ground_truths=canonicalize_ground_truths(sample.ground_truths),
                predictions=sample.predictions,
            )
            for sample in loaded.samples
        )

    ground_truths = tuple(gt for sample in samples for gt in sample.ground_truths)
    raw_count = len(loaded.ground_truths)
    canonical_count = len(ground_truths)
    return ProtocolBenchmarkView(
        samples=samples,
        ground_truths=ground_truths,
        predictions=loaded.predictions,
        runs_by_recording=loaded.runs_by_recording,
        recordings_by_id=loaded.recordings_by_id,
        ground_truth_accounting=GroundTruthAccounting(
            raw_count=raw_count,
            canonical_count=canonical_count,
            removed_count=raw_count - canonical_count,
        ),
    )
```

If type checking objects to `dict` is undesirable, use the concrete AnalysisRun/Recording types already imported by `loader.py`; do not weaken runtime semantics.

- [ ] **Step 5: Run protocol tests and existing AP tests**

```bash
pytest tests/test_benchmark_protocol.py tests/test_evaluation_ap.py tests/test_dataset_metrics.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add backend/app/benchmarks/schema.py backend/app/benchmarks/protocol.py backend/tests/test_benchmark_protocol.py
git commit -m "feat: add dataset benchmark protocol v2"
```

---

### Task 2: Make DatasetEvaluation creation and worker execution protocol-aware

**Files:**
- Modify: `backend/app/benchmarks/service.py`
- Modify: `backend/app/benchmarks/worker.py`
- Modify: `backend/tests/test_benchmark_membership.py`
- Modify: `backend/tests/test_benchmark_worker.py`

**Interfaces:**
- Consumes: `DEFAULT_PHYSICAL_TF_PROTOCOL`, `PROTOCOL_CONFIG_V1`, `PROTOCOL_CONFIG_V2`, `count_ground_truths_for_protocol()`, `build_protocol_view()` from Task 1.
- Produces: new DatasetEvaluations default to v2; pending/running/completed item `gt_count` is protocol-specific; worker metrics all consume one protocol view; v2 aggregate metrics include GT provenance.

- [ ] **Step 1: Add failing membership tests for v2 default and protocol-specific item counts**

In `backend/tests/test_benchmark_membership.py`, add tests equivalent to:

```python
from app.benchmarks.schema import PHYSICAL_TF_PROTOCOL_V2


def test_create_defaults_to_v2_and_item_gt_count_is_canonical(client):
    db = client.app.state.database
    with db.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        for gt_id in ("gt_1", "gt_2"):
            add_ground_truth(
                session, gt_id=gt_id, recording_id="rec_a", class_id=9, class_name="LoRa 250kHz",
                t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0,
            )
        add_run(session, run_id="run_a", recording_id="rec_a")
        session.commit()

    with db.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        preview = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        evaluation = svc.create_evaluation(
            name="v2", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=preview.recording_manifest_hash,
            items=[{"recording_id": "rec_a", "analysis_run_id": "run_a"}],
        )
        assert evaluation.evaluation_protocol == PHYSICAL_TF_PROTOCOL_V2
        assert evaluation.protocol_config_json["gt_duplicate_policy"] == "exact_physical_class_dedup"
        assert evaluation.items[0].gt_count == 1


def test_v2_creation_does_not_change_raw_manifest_or_raw_gt_rows(client):
    from sqlalchemy import func, select
    from app.ground_truth.model import GroundTruthModel

    db = client.app.state.database
    with db.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        for gt_id in ("gt_1", "gt_2"):
            add_ground_truth(
                session, gt_id=gt_id, recording_id="rec_a", class_id=9, class_name="LoRa 250kHz",
                t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0,
            )
        add_run(session, run_id="run_a", recording_id="rec_a")
        session.commit()

    with db.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        before = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        raw_before = session.scalar(select(func.count(GroundTruthModel.id)))
        evaluation = svc.create_evaluation(
            name="v2", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=before.recording_manifest_hash,
            items=[{"recording_id": "rec_a", "analysis_run_id": "run_a"}],
        )
        after = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        raw_after = session.scalar(select(func.count(GroundTruthModel.id)))
        assert raw_before == raw_after == 2
        assert before.recording_manifest_hash == after.recording_manifest_hash
        assert evaluation.items[0].gt_count == 1
```

- [ ] **Step 2: Add failing worker tests proving every v2 metric uses canonical GT and old v1 rows remain raw**

In `backend/tests/test_benchmark_worker.py`, add these concrete tests using the existing fixture helpers:

```python
from copy import deepcopy
from sqlalchemy import select

from app.benchmarks.model import DatasetEvaluationItemModel
from app.benchmarks.schema import PHYSICAL_TF_PROTOCOL_V1


def _build_duplicate_gt_evaluation(client, *, protocol=None):
    db = client.app.state.database
    with db.session_factory() as session:
        add_recording(session, recording_id="rec_dup", name="dup")
        for gt_id in ("gt_dup_1", "gt_dup_2"):
            add_ground_truth(
                session, gt_id=gt_id, recording_id="rec_dup", class_id=9, class_name="LoRa 250kHz",
                t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0,
            )
        add_run(session, run_id="run_dup", recording_id="rec_dup", executor="imported")
        add_detection(
            session, detection_id="det_dup", run_id="run_dup", class_id=9, class_name="LoRa 250kHz",
            confidence=0.9, t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0,
        )
        session.commit()

    with db.session_factory() as session:
        svc = DatasetBenchmarkService(session)
        preview = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
        kwargs = {} if protocol is None else {"evaluation_protocol": protocol}
        evaluation = svc.create_evaluation(
            name=f"dup-{protocol or 'default'}",
            dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=preview.recording_manifest_hash,
            items=[{"recording_id": "rec_dup", "analysis_run_id": "run_dup"}],
            **kwargs,
        )
        return evaluation.id


def test_v2_worker_uses_one_canonical_gt_for_every_metric(client, settings):
    evaluation_id = _build_duplicate_gt_evaluation(client)
    execute_benchmark(evaluation_id, settings)
    evaluation = _get(client, evaluation_id)
    aggregate = evaluation.aggregate_metrics_json

    assert aggregate["ground_truth"] == {
        "raw_count": 2,
        "canonical_count": 1,
        "duplicates_removed": 1,
        "duplicate_policy": "exact_physical_class_dedup",
    }
    assert aggregate["localization"]["operating"]["tp"] == 1
    assert aggregate["localization"]["operating"]["fn"] == 0
    assert aggregate["localization"]["ap50"] == 1.0
    assert aggregate["classification_on_matched"]["matched_count"] == 1
    assert aggregate["classification_on_matched"]["class_correct"] == 1
    assert aggregate["class_aware"]["operating"]["tp"] == 1
    assert aggregate["class_aware"]["operating"]["fn"] == 0
    assert evaluation.per_class_metrics_json[0]["gt_count"] == 1
    with client.app.state.database.session_factory() as session:
        item = session.scalar(
            select(DatasetEvaluationItemModel)
            .where(DatasetEvaluationItemModel.evaluation_id == evaluation_id)
        )
        assert item.gt_count == 1


def test_old_v1_row_without_new_gt_policy_config_still_uses_raw_gt(client, settings):
    evaluation_id = _build_duplicate_gt_evaluation(client, protocol=PHYSICAL_TF_PROTOCOL_V1)
    db = client.app.state.database
    with db.session_factory() as session:
        evaluation = session.get(DatasetEvaluationModel, evaluation_id)
        old_config = deepcopy(evaluation.protocol_config_json)
        old_config.pop("gt_duplicate_policy", None)
        old_config.pop("ground_truth_view", None)
        evaluation.protocol_config_json = old_config
        session.commit()

    execute_benchmark(evaluation_id, settings)
    evaluation = _get(client, evaluation_id)
    aggregate = evaluation.aggregate_metrics_json
    assert "ground_truth" not in aggregate
    assert aggregate["localization"]["operating"]["tp"] == 1
    assert aggregate["localization"]["operating"]["fn"] == 1
    with client.app.state.database.session_factory() as session:
        item = session.scalar(
            select(DatasetEvaluationItemModel)
            .where(DatasetEvaluationItemModel.evaluation_id == evaluation_id)
        )
        assert item.gt_count == 2
```

The v1 assertion intentionally proves the worker dispatches by `evaluation_protocol`, not by presence of the new descriptive config keys.

- [ ] **Step 3: Run focused tests and confirm RED**

```bash
pytest tests/test_benchmark_membership.py tests/test_benchmark_worker.py -q
```

Expected: failures because creation still freezes v1/raw counts and worker uses `loaded` directly.

- [ ] **Step 4: Make create_evaluation default to v2 and compute item counts from the protocol**

In `backend/app/benchmarks/service.py`:

```python
from app.benchmarks.protocol import count_ground_truths_for_protocol
from app.benchmarks.schema import (
    DEFAULT_PHYSICAL_TF_PROTOCOL,
    PHYSICAL_TF_PROTOCOL_V1,
    PHYSICAL_TF_PROTOCOL_V2,
    PROTOCOL_CONFIG_V1,
    PROTOCOL_CONFIG_V2,
)


def _protocol_config_for(protocol: str) -> dict:
    if protocol == PHYSICAL_TF_PROTOCOL_V1:
        return deepcopy(PROTOCOL_CONFIG_V1)
    if protocol == PHYSICAL_TF_PROTOCOL_V2:
        return deepcopy(PROTOCOL_CONFIG_V2)
    raise PlatformError("UNSUPPORTED_EVALUATION_PROTOCOL", f"Unsupported evaluation protocol: {protocol}", 422)
```

Extend the internal service signature only:

```python
def create_evaluation(
    self,
    *,
    name: str,
    dataset_name: str,
    dataset_split: str,
    label_space: str,
    recording_manifest_hash: str,
    items: list[dict],
    allow_incomplete: bool = False,
    evaluation_protocol: str = DEFAULT_PHYSICAL_TF_PROTOCOL,
) -> DatasetEvaluationModel:
```

Freeze:

```python
evaluation.evaluation_protocol = evaluation_protocol
evaluation.protocol_config_json = _protocol_config_for(evaluation_protocol)
```

and replace raw item count logic with:

```python
gt_count = count_ground_truths_for_protocol(
    evaluation_protocol,
    manifest_by_id[recording_id].ground_truth,
)
```

The FastAPI create schema must not expose `evaluation_protocol` in C-1; the standard API therefore always uses the v2 service default.

- [ ] **Step 5: Dispatch worker metrics through the protocol view**

In `backend/app/benchmarks/worker.py`:

```python
from app.benchmarks.protocol import build_protocol_view
```

Immediately after raw load:

```python
loaded = loader.load(evaluation_id)
view = build_protocol_view(evaluation.evaluation_protocol, loaded)
```

Use `view` everywhere metrics are computed:

```python
diagnostics = compute_dataset_diagnostics(
    list(view.samples),
    classification_applicable=applicable,
)
localization_ap = localization_ap_summary(
    list(view.ground_truths),
    list(view.predictions),
)
if applicable:
    class_aware_ap = class_aware_ap_summary(
        list(view.ground_truths),
        list(view.predictions),
    )
```

Pass accounting to `_build_result_jsons` and add the block only for v2:

```python
if evaluation.evaluation_protocol == PHYSICAL_TF_PROTOCOL_V2:
    aggregate["ground_truth"] = {
        "raw_count": view.ground_truth_accounting.raw_count,
        "canonical_count": view.ground_truth_accounting.canonical_count,
        "duplicates_removed": view.ground_truth_accounting.removed_count,
        "duplicate_policy": "exact_physical_class_dedup",
    }
```

When finalizing items, look up the protocol-specific sample:

```python
sample_by_recording = {sample.recording_id: sample for sample in view.samples}
for item in evaluation.items:
    sample = sample_by_recording.get(item.recording_id)
    if sample is not None:
        assert item.gt_count == len(sample.ground_truths)
        item.prediction_count = len(sample.predictions)
```

Do not reset v1 item counts or rewrite old v1 config.

- [ ] **Step 6: Run focused worker/membership tests**

```bash
pytest tests/test_benchmark_membership.py tests/test_benchmark_worker.py tests/test_benchmark_protocol.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add backend/app/benchmarks/service.py backend/app/benchmarks/worker.py backend/tests/test_benchmark_membership.py backend/tests/test_benchmark_worker.py
git commit -m "feat: apply benchmark protocol views consistently"
```

---

### Task 3: Add derived Imported Batch Catalog and exact resolver service

**Files:**
- Modify: `backend/tests/benchmark_fixture.py`
- Create: `backend/tests/test_benchmark_imported_batch.py`
- Modify: `backend/app/benchmarks/service.py`

**Interfaces:**
- Consumes: `AnalysisRunModel.parameters_json["batch_import"]`, Recording dataset metadata, DetectionResult counts, current frozen manifest builder.
- Produces:
  - `ImportedBatchCatalogEntry`
  - `ImportedBatchResolutionEntry`
  - `ImportedBatchResolutionPreview`
  - `DatasetBenchmarkService.list_imported_batches()`
  - `DatasetBenchmarkService.resolve_imported_batch(import_fingerprint: str)`

- [ ] **Step 1: Extend benchmark fixture to persist batch provenance explicitly**

Change `add_run()` in `backend/tests/benchmark_fixture.py` to accept a caller-supplied parameter payload:

```python
def add_run(session, *, run_id, recording_id, pipeline_id="pipeline_x", pipeline_version="1.0",
            executor="imported", status="completed", created_at=None, parameters_json=None):
    session.add(AnalysisRunModel(
        id=run_id,
        recording_id=recording_id,
        pipeline_id=pipeline_id,
        pipeline_version=pipeline_version,
        executor=executor,
        status=status,
        parameters_json=parameters_json or {},
        created_at=created_at,
    ))
    return run_id
```

- [ ] **Step 2: Write failing catalog/resolver tests**

Create `backend/tests/test_benchmark_imported_batch.py` with a helper:

```python
FINGERPRINT = "a" * 64


def batch_parameters(item_key: str, *, fingerprint=FINGERPRINT, recording_manifest_hash=None):
    batch_import = {
            "schema_version": 1,
            "batch_id": "batch_x",
            "item_key": item_key,
            "package_path": f"items/{item_key}.analysis.zip",
            "import_fingerprint": fingerprint,
            "recording_fingerprint": "b" * 64,
            "archive_sha256": "c" * 64,
            "result_provenance": {"source_predictions_sha256": "d" * 64},
            "transport_provenance": {"exporter_version": "m8_6b_v1"},
        }
    if recording_manifest_hash is not None:
        batch_import["recording_manifest_hash"] = recording_manifest_hash
    return {"batch_import": batch_import}
```

Add these concrete service tests (the helper deliberately creates valid GT-bearing SpaceNet recordings so the frozen manifest can be rebuilt):

```python
import pytest

from benchmark_fixture import add_detection, add_ground_truth, add_recording, add_run
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError

FINGERPRINT = "a" * 64


def seed_recording(session, recording_id: str, name: str):
    add_recording(session, recording_id=recording_id, name=name)
    add_ground_truth(
        session,
        gt_id=f"gt_{recording_id}",
        recording_id=recording_id,
        class_id=9,
        class_name="LoRa 250kHz",
        t0=0.01,
        t1=0.02,
        f0=2_440_600_000.0,
        f1=2_440_700_000.0,
    )


def seed_batch_run(session, *, recording_id: str, run_id: str, item_key: str,
                   fingerprint: str = FINGERPRINT, pipeline_version: str = "1.0",
                   executor: str = "imported", status: str = "completed",
                   recording_manifest_hash: str | None = None):
    add_run(
        session,
        run_id=run_id,
        recording_id=recording_id,
        pipeline_id="pipeline_x",
        pipeline_version=pipeline_version,
        executor=executor,
        status=status,
        parameters_json=batch_parameters(
            item_key, fingerprint=fingerprint, recording_manifest_hash=recording_manifest_hash
        ),
    )
    add_detection(
        session,
        detection_id=f"det_{run_id}",
        run_id=run_id,
        class_id=9,
        class_name="LoRa 250kHz",
        confidence=0.9,
        t0=0.01,
        t1=0.02,
        f0=2_440_600_000.0,
        f1=2_440_700_000.0,
    )


def test_catalog_contains_only_completed_imported_batch_runs(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="a")
        seed_batch_run(session, recording_id="rec_b", run_id="run_b", item_key="b")
        add_run(session, run_id="run_local", recording_id="rec_a", executor="local_cpu")
        add_run(session, run_id="run_plain_import", recording_id="rec_b", executor="imported")
        seed_batch_run(session, recording_id="rec_b", run_id="run_failed", item_key="failed", status="failed")
        session.commit()
        entries = DatasetBenchmarkService(session).list_imported_batches()

    assert len(entries) == 1
    entry = entries[0]
    assert entry.import_fingerprint == FINGERPRINT
    assert entry.run_count == 2
    assert entry.detection_count == 2
    assert entry.ready is True
    assert entry.inconsistency_reasons == ()


def test_resolver_returns_exact_manifest_mapping(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="a")
        seed_batch_run(session, recording_id="rec_b", run_id="run_b", item_key="b")
        session.commit()
        svc = DatasetBenchmarkService(session)
        expected_hash = svc.prepare_manifest("SpaceNet", "test", "spacenet_14").recording_manifest_hash
        preview = svc.resolve_imported_batch(FINGERPRINT)

    assert preview.dataset_name == "SpaceNet"
    assert preview.dataset_split == "test"
    assert preview.label_space == "spacenet_14"
    assert preview.pipeline_id == "pipeline_x"
    assert preview.pipeline_version == "1.0"
    assert preview.recording_manifest_hash == expected_hash
    assert preview.expected_recordings == 2
    assert preview.resolved_recordings == 2
    assert preview.missing_recordings == 0
    assert preview.conflict_count == 0
    assert [(x.recording_id, x.analysis_run_id) for x in preview.entries] == [
        ("rec_a", "run_a"), ("rec_b", "run_b")
    ]


def test_resolver_rejects_duplicate_item_key(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="same")
        seed_batch_run(session, recording_id="rec_b", run_id="run_b", item_key="same")
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_STATE_INCONSISTENT"


def test_resolver_rejects_duplicate_recording_mapping(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a1", item_key="a1")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a2", item_key="a2")
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_STATE_INCONSISTENT"


def test_resolver_rejects_mixed_pipeline(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="a", pipeline_version="1.0")
        seed_batch_run(session, recording_id="rec_b", run_id="run_b", item_key="b", pipeline_version="2.0")
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_STATE_INCONSISTENT"


def test_resolver_rejects_incomplete_current_manifest(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        seed_batch_run(session, recording_id="rec_a", run_id="run_a", item_key="a")
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_DATASET_INCOMPLETE"


def test_resolver_rejects_present_but_mismatched_manifest_provenance(client):
    with client.app.state.database.session_factory() as session:
        seed_recording(session, "rec_a", "a")
        seed_recording(session, "rec_b", "b")
        wrong = "e" * 64
        seed_batch_run(
            session, recording_id="rec_a", run_id="run_a", item_key="a",
            recording_manifest_hash=wrong,
        )
        seed_batch_run(
            session, recording_id="rec_b", run_id="run_b", item_key="b",
            recording_manifest_hash=wrong,
        )
        session.commit()
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch(FINGERPRINT)
    assert exc.value.code == "IMPORTED_BATCH_DATASET_INCOMPLETE"


def test_resolver_not_found(client):
    with client.app.state.database.session_factory() as session:
        with pytest.raises(PlatformError) as exc:
            DatasetBenchmarkService(session).resolve_imported_batch("f" * 64)
    assert exc.value.code == "IMPORTED_BATCH_NOT_FOUND"
```


- [ ] **Step 3: Run imported-batch tests and confirm RED**

```bash
pytest tests/test_benchmark_imported_batch.py -q
```

Expected: service methods/dataclasses missing.

- [ ] **Step 4: Add imported-batch read-model dataclasses and query helpers**

In `backend/app/benchmarks/service.py`, add focused dataclasses near existing preview dataclasses:

```python
@dataclass(frozen=True)
class ImportedBatchCatalogEntry:
    import_fingerprint: str
    pipeline_id: str | None
    pipeline_version: str | None
    dataset_name: str | None
    dataset_split: str | None
    label_space: str | None
    run_count: int
    detection_count: int
    archive_sha256: str | None
    result_provenance: dict
    transport_provenance: dict
    ready: bool
    inconsistency_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ImportedBatchResolutionEntry:
    manifest_order: int
    recording_id: str
    recording_name: str
    analysis_run_id: str
    item_key: str


@dataclass(frozen=True)
class ImportedBatchResolutionPreview:
    import_fingerprint: str
    dataset_name: str
    dataset_split: str
    label_space: str
    pipeline_id: str
    pipeline_version: str
    recording_manifest_hash: str
    expected_recordings: int
    resolved_recordings: int
    missing_recordings: int
    conflict_count: int
    entries: tuple[ImportedBatchResolutionEntry, ...]
```

Add these private helpers so both catalog and resolver share the same run selection semantics:

```python
import re

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _batch_import_payload(run: AnalysisRunModel) -> dict:
    payload = (run.parameters_json or {}).get("batch_import")
    return payload if isinstance(payload, dict) else {}


def _batch_runs(self, import_fingerprint: str | None = None) -> list[AnalysisRunModel]:
    candidates = list(self.session.scalars(
        select(AnalysisRunModel)
        .where(
            AnalysisRunModel.executor == "imported",
            AnalysisRunModel.status == "completed",
        )
        .order_by(AnalysisRunModel.id)
    ).all())
    selected = []
    for run in candidates:
        fingerprint = _batch_import_payload(run).get("import_fingerprint")
        if not isinstance(fingerprint, str) or _SHA256_RE.fullmatch(fingerprint) is None:
            continue
        if import_fingerprint is None or fingerprint == import_fingerprint:
            selected.append(run)
    return selected


def _detection_counts(self, run_ids: list[str]) -> dict[str, int]:
    if not run_ids:
        return {}
    return {
        run_id: int(count)
        for run_id, count in self.session.execute(
            select(DetectionResultModel.run_id, func.count(DetectionResultModel.id))
            .where(DetectionResultModel.run_id.in_(run_ids))
            .group_by(DetectionResultModel.run_id)
        ).all()
    }
```

This deliberately excludes ordinary imported single-run packages, local CPU runs, and failed/interrupted batch runs before grouping.

- [ ] **Step 5: Implement strict resolver validation**

Implement the resolver with explicit cardinality and current-manifest checks:

```python
def resolve_imported_batch(self, import_fingerprint: str) -> ImportedBatchResolutionPreview:
    runs = self._batch_runs(import_fingerprint)
    if not runs:
        raise PlatformError("IMPORTED_BATCH_NOT_FOUND", "Imported batch was not found.", 404)

    metas = [_batch_import_payload(run) for run in runs]
    item_keys = [meta.get("item_key") for meta in metas]
    if any(not isinstance(key, str) or not key for key in item_keys) or len(set(item_keys)) != len(item_keys):
        raise PlatformError(
            "IMPORTED_BATCH_STATE_INCONSISTENT",
            "Imported batch has missing or duplicate item keys.",
            409,
        )

    recording_ids = [run.recording_id for run in runs]
    if len(set(recording_ids)) != len(recording_ids):
        raise PlatformError(
            "IMPORTED_BATCH_STATE_INCONSISTENT",
            "Imported batch maps more than one completed run to a Recording.",
            409,
        )

    pipeline_ids = {run.pipeline_id for run in runs}
    pipeline_versions = {run.pipeline_version for run in runs}
    if len(pipeline_ids) != 1 or len(pipeline_versions) != 1:
        raise PlatformError(
            "IMPORTED_BATCH_STATE_INCONSISTENT",
            "Imported batch contains mixed pipeline id/version values.",
            409,
        )

    recordings = {
        recording.id: recording
        for recording in self.session.scalars(
            select(RecordingModel).where(RecordingModel.id.in_(recording_ids))
        ).all()
    }
    if set(recordings) != set(recording_ids):
        raise PlatformError(
            "IMPORTED_BATCH_STATE_INCONSISTENT",
            "Imported batch references a missing Recording.",
            409,
        )

    dataset_keys = {
        (recording.dataset_name, recording.dataset_split, recording.label_space)
        for recording in recordings.values()
    }
    if len(dataset_keys) != 1 or any(value is None for value in next(iter(dataset_keys))):
        raise PlatformError(
            "IMPORTED_BATCH_STATE_INCONSISTENT",
            "Imported batch Recordings do not share one dataset/split/label-space identity.",
            409,
        )
    dataset_name, dataset_split, label_space = next(iter(dataset_keys))
    frozen = self._build_frozen_manifest(dataset_name, dataset_split, label_space)
    frozen_ids = {entry.recording_id for entry in frozen.entries}
    if set(recording_ids) != frozen_ids:
        raise PlatformError(
            "IMPORTED_BATCH_DATASET_INCOMPLETE",
            "Imported batch does not cover the current frozen Recording manifest exactly.",
            422,
        )

    # M8.6B did not persist the outer manifest hash per run. Future provenance may.
    # If present, it is a consistency assertion, not the source of truth.
    persisted_manifest_hashes = {
        meta.get("recording_manifest_hash")
        for meta in metas
        if meta.get("recording_manifest_hash") is not None
    }
    if persisted_manifest_hashes and persisted_manifest_hashes != {frozen.sha256}:
        raise PlatformError(
            "IMPORTED_BATCH_DATASET_INCOMPLETE",
            "Imported batch manifest provenance does not match the current frozen Recording manifest.",
            422,
        )

    run_by_recording = {run.recording_id: run for run in runs}
    item_key_by_recording = {run.recording_id: _batch_import_payload(run)["item_key"] for run in runs}
    entries = tuple(
        ImportedBatchResolutionEntry(
            manifest_order=index,
            recording_id=entry.recording_id,
            recording_name=entry.name,
            analysis_run_id=run_by_recording[entry.recording_id].id,
            item_key=item_key_by_recording[entry.recording_id],
        )
        for index, entry in enumerate(frozen.entries)
    )
    return ImportedBatchResolutionPreview(
        import_fingerprint=import_fingerprint,
        dataset_name=dataset_name,
        dataset_split=dataset_split,
        label_space=label_space,
        pipeline_id=next(iter(pipeline_ids)),
        pipeline_version=next(iter(pipeline_versions)),
        recording_manifest_hash=frozen.sha256,
        expected_recordings=len(frozen.entries),
        resolved_recordings=len(entries),
        missing_recordings=0,
        conflict_count=0,
        entries=entries,
    )
```

Never fill a missing Recording with a different completed run, and never fall back to `created_at` ordering or "latest" semantics.

- [ ] **Step 6: Implement catalog using the same validation facts**

Implement `list_imported_batches()` by grouping the already-filtered completed imported runs once, bulk-counting detections, and using the strict resolver as the readiness oracle:

```python
def _only_or_none(values):
    unique = set(values)
    return next(iter(unique)) if len(unique) == 1 else None


def list_imported_batches(self) -> list[ImportedBatchCatalogEntry]:
    runs = self._batch_runs()
    groups: dict[str, list[AnalysisRunModel]] = {}
    for run in runs:
        fingerprint = _batch_import_payload(run)["import_fingerprint"]
        groups.setdefault(fingerprint, []).append(run)

    all_run_ids = [run.id for run in runs]
    detection_counts = self._detection_counts(all_run_ids)
    recording_ids = {run.recording_id for run in runs}
    recordings = {
        recording.id: recording
        for recording in self.session.scalars(
            select(RecordingModel).where(RecordingModel.id.in_(recording_ids))
        ).all()
    } if recording_ids else {}

    entries = []
    for fingerprint in sorted(groups):
        group = sorted(groups[fingerprint], key=lambda run: run.id)
        metas = [_batch_import_payload(run) for run in group]
        group_recordings = [recordings.get(run.recording_id) for run in group]
        valid_recordings = [recording for recording in group_recordings if recording is not None]

        pipeline_id = _only_or_none(run.pipeline_id for run in group)
        pipeline_version = _only_or_none(run.pipeline_version for run in group)
        dataset_name = _only_or_none(recording.dataset_name for recording in valid_recordings)
        dataset_split = _only_or_none(recording.dataset_split for recording in valid_recordings)
        label_space = _only_or_none(recording.label_space for recording in valid_recordings)
        archive_sha256 = _only_or_none(meta.get("archive_sha256") for meta in metas)

        result_payloads = [meta.get("result_provenance") or {} for meta in metas]
        transport_payloads = [meta.get("transport_provenance") or {} for meta in metas]
        result_provenance = result_payloads[0] if all(x == result_payloads[0] for x in result_payloads) else {}
        transport_provenance = transport_payloads[0] if all(x == transport_payloads[0] for x in transport_payloads) else {}

        try:
            resolved = self.resolve_imported_batch(fingerprint)
            ready = True
            reasons: tuple[str, ...] = ()
            pipeline_id = resolved.pipeline_id
            pipeline_version = resolved.pipeline_version
            dataset_name = resolved.dataset_name
            dataset_split = resolved.dataset_split
            label_space = resolved.label_space
        except PlatformError as exc:
            ready = False
            reasons = (exc.code,)

        entries.append(ImportedBatchCatalogEntry(
            import_fingerprint=fingerprint,
            pipeline_id=pipeline_id,
            pipeline_version=pipeline_version,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            label_space=label_space,
            run_count=len(group),
            detection_count=sum(detection_counts.get(run.id, 0) for run in group),
            archive_sha256=archive_sha256,
            result_provenance=result_provenance,
            transport_provenance=transport_provenance,
            ready=ready,
            inconsistency_reasons=reasons,
        ))

    return sorted(
        entries,
        key=lambda item: (
            item.dataset_name or "", item.dataset_split or "",
            item.pipeline_id or "", item.pipeline_version or "", item.import_fingerprint,
        ),
    )
```

Invalid/incomplete groups remain visible as `ready=False` with the resolver error code and cannot be selected by the C-3 create flow. The catalog is read-only and never repairs provenance.

- [ ] **Step 7: Run focused service tests**

```bash
pytest tests/test_benchmark_imported_batch.py tests/test_benchmark_membership.py tests/test_batch_import_service.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add backend/tests/benchmark_fixture.py backend/tests/test_benchmark_imported_batch.py backend/app/benchmarks/service.py
git commit -m "feat: resolve imported benchmark batches exactly"
```

---

### Task 4: Expose imported-batch catalog and resolver APIs

**Files:**
- Modify: `backend/app/benchmarks/schema.py`
- Modify: `backend/app/benchmarks/router.py`
- Modify: `backend/tests/test_benchmark_api.py`

**Interfaces:**
- Consumes: service dataclasses/methods from Task 3.
- Produces:
  - `GET /api/dataset-benchmarks/imported-batches`
  - `POST /api/dataset-benchmarks/resolve-imported-batch`
  - standard `POST /api/dataset-benchmarks` still accepts the same create payload but now creates v2.

- [ ] **Step 1: Add failing API tests**

Extend `backend/tests/test_benchmark_api.py` with full fixture setup and these assertions:

```python
from benchmark_fixture import add_ground_truth, add_recording, add_run

API_FINGERPRINT = "a" * 64


def _api_batch_parameters(item_key: str):
    return {
        "batch_import": {
            "schema_version": 1,
            "batch_id": "batch_api",
            "item_key": item_key,
            "package_path": f"items/{item_key}.analysis.zip",
            "import_fingerprint": API_FINGERPRINT,
            "recording_fingerprint": "b" * 64,
            "archive_sha256": "c" * 64,
            "result_provenance": {},
            "transport_provenance": {"exporter_version": "m8_6b_v1"},
        }
    }


def _seed_api_batch(client):
    with client.app.state.database.session_factory() as session:
        for recording_id, name, run_id in (("rec_a", "a", "run_a"), ("rec_b", "b", "run_b")):
            add_recording(session, recording_id=recording_id, name=name)
            add_ground_truth(
                session, gt_id=f"gt_{recording_id}", recording_id=recording_id,
                class_id=9, class_name="LoRa 250kHz",
                t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0,
            )
            add_run(
                session, run_id=run_id, recording_id=recording_id, pipeline_id="pipeline_x",
                pipeline_version="1.0", executor="imported", status="completed",
                parameters_json=_api_batch_parameters(name),
            )
        session.commit()


def test_imported_batch_catalog_api(client):
    _seed_api_batch(client)
    response = client.get("/api/dataset-benchmarks/imported-batches")
    assert response.status_code == 200
    item = response.json()[0]
    assert item["import_fingerprint"] == API_FINGERPRINT
    assert item["run_count"] == 2
    assert item["ready"] is True


def test_resolve_imported_batch_api(client):
    _seed_api_batch(client)
    response = client.post(
        "/api/dataset-benchmarks/resolve-imported-batch",
        json={"import_fingerprint": API_FINGERPRINT},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["resolved_recordings"] == payload["expected_recordings"] == 2
    assert payload["missing_recordings"] == 0
    assert {x["analysis_run_id"] for x in payload["entries"]} == {"run_a", "run_b"}


def test_standard_create_api_now_freezes_v2(client):
    _seed_api_batch(client)
    resolved = client.post(
        "/api/dataset-benchmarks/resolve-imported-batch",
        json={"import_fingerprint": API_FINGERPRINT},
    ).json()
    response = client.post(
        "/api/dataset-benchmarks",
        json={
            "name": "api-v2",
            "dataset_name": resolved["dataset_name"],
            "dataset_split": resolved["dataset_split"],
            "label_space": resolved["label_space"],
            "recording_manifest_hash": resolved["recording_manifest_hash"],
            "allow_incomplete": False,
            "items": [
                {"recording_id": item["recording_id"], "analysis_run_id": item["analysis_run_id"]}
                for item in resolved["entries"]
            ],
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["evaluation_protocol"] == "physical_tf_detection_ap_v2"
    assert payload["protocol_config_json"]["gt_duplicate_policy"] == "exact_physical_class_dedup"
```

- [ ] **Step 2: Run API tests and confirm RED**

```bash
pytest tests/test_benchmark_api.py -q
```

Expected: new routes return 404 / schemas missing.

- [ ] **Step 3: Add Pydantic wire models**

In `backend/app/benchmarks/schema.py`:

```python
class ImportedBatchResolveRequest(BaseModel):
    import_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImportedBatchCatalogRead(BaseModel):
    import_fingerprint: str
    pipeline_id: str | None
    pipeline_version: str | None
    dataset_name: str | None
    dataset_split: str | None
    label_space: str | None
    run_count: int
    detection_count: int
    archive_sha256: str | None
    result_provenance: dict
    transport_provenance: dict
    ready: bool
    inconsistency_reasons: list[str]


class ImportedBatchResolutionEntryRead(BaseModel):
    manifest_order: int
    recording_id: str
    recording_name: str
    analysis_run_id: str
    item_key: str


class ImportedBatchResolutionPreviewRead(BaseModel):
    import_fingerprint: str
    dataset_name: str
    dataset_split: str
    label_space: str
    pipeline_id: str
    pipeline_version: str
    recording_manifest_hash: str
    expected_recordings: int
    resolved_recordings: int
    missing_recordings: int
    conflict_count: int
    entries: list[ImportedBatchResolutionEntryRead]
```

- [ ] **Step 4: Add routes before the `/{evaluation_id}` catch-all route**

In `backend/app/benchmarks/router.py`, define static routes before `@router.get("/{evaluation_id}")`:

```python
@router.get("/imported-batches", response_model=list[ImportedBatchCatalogRead])
def list_imported_batches(request: Request):
    with request.app.state.database.session_factory() as session:
        return [entry.__dict__ for entry in _service(request, session).list_imported_batches()]


@router.post("/resolve-imported-batch", response_model=ImportedBatchResolutionPreviewRead)
def resolve_imported_batch(payload: ImportedBatchResolveRequest, request: Request):
    with request.app.state.database.session_factory() as session:
        preview = _service(request, session).resolve_imported_batch(payload.import_fingerprint)
        return {
            **{k: v for k, v in preview.__dict__.items() if k != "entries"},
            "entries": [entry.__dict__ for entry in preview.entries],
        }
```

Convert tuple fields such as `inconsistency_reasons` to list if Pydantic does not coerce them under current settings.

- [ ] **Step 5: Run API + service regression tests**

```bash
pytest tests/test_benchmark_api.py tests/test_benchmark_imported_batch.py tests/test_benchmark_worker.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/app/benchmarks/schema.py backend/app/benchmarks/router.py backend/tests/test_benchmark_api.py
git commit -m "feat: expose imported batch benchmark resolution"
```

---

### Task 5: C-1 real-data dry-run and full regression gate

**Files:**
- No product code expected.
- Create after evidence is collected: `docs/research/m8_6c_protocol_membership_foundation.md`

**Interfaces:**
- Consumes: completed Tasks 1–4 and the existing local `platform.db` containing the M8.6B import.
- Produces: evidence that the exact historical semantic fingerprint resolves 2500/2500 without starting a DatasetEvaluation worker.

- [ ] **Step 1: Run the complete backend suite before touching real data**

From `backend/`:

```bash
pytest -q
```

Expected: all tests PASS; do not proceed on any failure.

- [ ] **Step 2: Prove C-1 is pointed at the intended real local database, then run the read-only resolver probe**

First verify settings from `backend/`:

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

Stop if `WSP_DATABASE_URL` or another setting points elsewhere. Then run the read-only resolver probe:

```bash
python - <<'PY'
from app.benchmarks.service import DatasetBenchmarkService
from app.core.config import Settings
from app.db.base import Base, load_domain_models
from app.db.session import Database

FP = "c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5"
settings = Settings()
db = Database(settings.database_url)
load_domain_models()
with db.session_factory() as session:
    svc = DatasetBenchmarkService(session)
    preview = svc.resolve_imported_batch(FP)
    print("fingerprint", preview.import_fingerprint)
    print("dataset", preview.dataset_name, preview.dataset_split, preview.label_space)
    print("pipeline", preview.pipeline_id, preview.pipeline_version)
    print("manifest", preview.recording_manifest_hash)
    print("coverage", preview.resolved_recordings, "/", preview.expected_recordings)
    print("missing", preview.missing_recordings, "conflicts", preview.conflict_count)
    print("unique_runs", len({item.analysis_run_id for item in preview.entries}))
PY
```

Required evidence:

```text
fingerprint c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5
dataset SpaceNet test spacenet_14
pipeline zoomspec_yolo26n_aug_combined_frn_v3 1.0.0
manifest 91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b
coverage 2500 / 2500
missing 0 conflicts 0
unique_runs 2500
```

This probe must not call `create_evaluation`, `start_evaluation`, or `execute_benchmark`.

- [ ] **Step 3: Verify raw SpaceNet GT remains 20018**

```bash
python - <<'PY'
from sqlalchemy import func, select
from app.core.config import Settings
from app.db.session import Database
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

settings = Settings()
db = Database(settings.database_url)
with db.session_factory() as session:
    count = session.scalar(
        select(func.count(GroundTruthModel.id))
        .join(RecordingModel, GroundTruthModel.recording_id == RecordingModel.id)
        .where(
            RecordingModel.dataset_name == "SpaceNet",
            RecordingModel.dataset_split == "test",
            RecordingModel.label_space == "spacenet_14",
        )
    )
    print(count)
PY
```

Required output: `20018`.

- [ ] **Step 4: Record C-1 evidence**

Create `docs/research/m8_6c_protocol_membership_foundation.md` with exact observed test count, resolver output, raw GT count, branch commit, and an explicit statement:

```text
No formal 2500-sample DatasetEvaluation was started in C-1.
```

Do not put historical mAP claims in this note; those belong to C-2.

- [ ] **Step 5: Commit C-1 acceptance evidence**

```bash
git add docs/research/m8_6c_protocol_membership_foundation.md
git commit -m "docs: record m8.6c-1 protocol membership gate"
```

- [ ] **Step 6: Gate decision**

C-1 is PASS only if all of the following are true:

```text
backend full suite green
v1 raw behavior green
v2 exact dedup behavior green
raw DB GT still 20018
historical fingerprint resolves exactly 2500 unique runs
manifest hash == 91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b
no formal DatasetEvaluation worker started
working tree clean
```

Stop here and report the evidence for review. Do not begin C-2 until this gate is accepted.

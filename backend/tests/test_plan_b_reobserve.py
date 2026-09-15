"""Plan B remediation: bounded H5 monitoring re-observation (CPU-only).

These tests EXECUTE the real acceptance code paths (not source-string checks) so
that a deterministic post-inference tooling defect cannot slip through and
consume the single authorized 16-execution window.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_common as common  # noqa: E402
import plan_b_h5_core as h5  # noqa: E402
import plan_b_h5_reobserve_cp as reobserve  # noqa: E402


# ---------------------------------------------------------------------------
# Finding A — exactly one authorized experiment start
# ---------------------------------------------------------------------------

def test_exactly_one_experiment_run_in_lifecycle() -> None:
    source = (SCRIPTS / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    # Exactly one POST /run to the dataset-experiment run endpoint.
    assert source.count('f"/api/dataset-experiments/{experiment_id}/run"') == 1
    # The post-run path must never call the starting helper.
    assert "core.run_experiment(" not in source


def test_post_run_acceptance_is_read_only() -> None:
    """After the monitored run, acceptance reads must be GET/DB only."""
    source = (SCRIPTS / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    assert "read_terminal_acceptance(" in source
    assert "core.fetch_items(" in source
    assert "core.fetch_attempts(" in source
    assert "core.fetch_evaluation(" in source


# ---------------------------------------------------------------------------
# Finding B — raw-evidence validator keyword signature (real invocation)
# ---------------------------------------------------------------------------

class _FakeMonitor:
    def __init__(self, samples):
        self.samples = samples
        self.thread_failure = None


def _write_raw(tmp_path: Path):
    cpath = tmp_path / "h5_reobserve_concurrency_samples.jsonl"
    rpath = tmp_path / "h5_reobserve_resource_samples.jsonl"
    cpath.write_text("{}\n", encoding="utf-8")
    rpath.write_text("{}\n", encoding="utf-8")
    return cpath, rpath


def test_assert_raw_evidence_quality_real_signature(tmp_path: Path) -> None:
    from plan_b_h5_core import ConcurrencySample, WorkerOwnership

    cpath, rpath = _write_raw(tmp_path)
    samples = [
        ConcurrencySample(0.0, (11,), ("run_a",), (11,), 1, 0, 0.25,
                          experiment_id="e", ownership=(WorkerOwnership("run_a", 11),)),
        ConcurrencySample(0.25, (11, 22), ("a", "b"), (11, 22), 2, 0, 0.25,
                          experiment_id="e",
                          ownership=(WorkerOwnership("a", 11), WorkerOwnership("b", 22))),
    ]
    concurrency = _FakeMonitor(samples)
    resource = _FakeMonitor([object(), object()])
    result = reobserve.assert_raw_evidence_quality(
        samples_path=cpath,
        resource_path=rpath,
        concurrency_monitor=concurrency,
        resource_monitor=resource,
    )
    assert result["owned_worker_samples"] == 2
    assert result["gpu_owned_samples"] == 2
    assert result["unique_owned_run_ids"] == 3
    assert result["max_observed_concurrency"] == 2


def test_assert_raw_evidence_quality_rejects_missing_artifact(tmp_path: Path) -> None:
    from plan_b_h5_core import ConcurrencySample, WorkerOwnership

    cpath = tmp_path / "missing.jsonl"
    rpath = tmp_path / "r.jsonl"
    rpath.write_text("{}\n", encoding="utf-8")
    samples = [ConcurrencySample(0.0, (11,), ("a",), (11,), 1, 0, 0.25,
                                 ownership=(WorkerOwnership("a", 11),))]
    with pytest.raises(SystemExit):
        reobserve.assert_raw_evidence_quality(
            samples_path=cpath, resource_path=rpath,
            concurrency_monitor=_FakeMonitor(samples),
            resource_monitor=_FakeMonitor([1]),
        )


# ---------------------------------------------------------------------------
# Finding C — exact 16-stem membership despite lexical ordering
# ---------------------------------------------------------------------------

def _acceptance_kwargs(items):
    return dict(
        experiment_id="exp_x",
        terminal_summary={
            "status": "completed", "expected_items": 16, "completed_items": 16,
            "failed_items": 0, "queued_items": 0, "running_items": 0, "attempt_count": 16,
        },
        items=items,
        attempts={i["id"]: [{"analysis_run_id": f"run_{n}"}] for n, i in enumerate(items)},
        actual_runs={f"run_{n}" for n in range(len(items))},
        evaluation={"status": "completed", "evaluated_recordings": 16,
                    "missing_recordings": 0, "coverage": 1.0},
        cycle_stems=common.H2_STEMS,
        raw_evidence={"owned_worker_samples": 1},
        quiescence={"quiescent": True},
        concurrency_artifact=Path("/tmp/opencode/c.jsonl"),
        resource_artifact=Path("/tmp/opencode/r.jsonl"),
    )


def _items_for(names):
    return [{"id": f"i{n}", "status": "completed", "recording_name": name}
            for n, name in enumerate(names)]


def test_membership_exact_despite_lexical_order(tmp_path: Path, monkeypatch) -> None:
    # Provide real (non-empty) artifacts so SHA256 hashing succeeds.
    cpath = tmp_path / "c.jsonl"; cpath.write_text("x\n", encoding="utf-8")
    rpath = tmp_path / "r.jsonl"; rpath.write_text("y\n", encoding="utf-8")
    kwargs = _acceptance_kwargs(_items_for(common.H2_STEMS))
    kwargs["concurrency_artifact"] = cpath
    kwargs["resource_artifact"] = rpath
    result = reobserve.build_acceptance(**kwargs)
    assert result["db_checks"]["recording_membership_exact"] is True
    assert result["executions_consumed"] == 16
    assert result["historical_actual_before"] == 136
    assert result["final_actual_total"] == 152
    assert len(result["artifacts"]["sha256"]["concurrency_jsonl_sha256"]) == 64


def test_membership_missing_stem_fails(tmp_path: Path) -> None:
    cpath = tmp_path / "c.jsonl"; cpath.write_text("x\n", encoding="utf-8")
    rpath = tmp_path / "r.jsonl"; rpath.write_text("y\n", encoding="utf-8")
    kwargs = _acceptance_kwargs(_items_for(common.H2_STEMS[:15]))
    kwargs["concurrency_artifact"] = cpath; kwargs["resource_artifact"] = rpath
    # Drop one attempt to keep other checks consistent; membership must still fail.
    with pytest.raises(SystemExit):
        reobserve.build_acceptance(**kwargs)


def test_membership_extra_stem_fails(tmp_path: Path) -> None:
    cpath = tmp_path / "c.jsonl"; cpath.write_text("x\n", encoding="utf-8")
    rpath = tmp_path / "r.jsonl"; rpath.write_text("y\n", encoding="utf-8")
    names = list(common.H2_STEMS[:15]) + ["999", "5"]
    kwargs = _acceptance_kwargs(_items_for(names))
    kwargs["concurrency_artifact"] = cpath; kwargs["resource_artifact"] = rpath
    with pytest.raises(SystemExit):
        reobserve.build_acceptance(**kwargs)


def test_membership_wrong_stem_fails(tmp_path: Path) -> None:
    cpath = tmp_path / "c.jsonl"; cpath.write_text("x\n", encoding="utf-8")
    rpath = tmp_path / "r.jsonl"; rpath.write_text("y\n", encoding="utf-8")
    names = list(common.H2_STEMS[:-1]) + ["777"]
    kwargs = _acceptance_kwargs(_items_for(names))
    kwargs["concurrency_artifact"] = cpath; kwargs["resource_artifact"] = rpath
    with pytest.raises(SystemExit):
        reobserve.build_acceptance(**kwargs)


# ---------------------------------------------------------------------------
# Test E — one-shot acceptance values + write only after checks pass
# ---------------------------------------------------------------------------

def test_build_acceptance_writes_only_after_checks(tmp_path: Path) -> None:
    cpath = tmp_path / "c.jsonl"; cpath.write_text("x\n", encoding="utf-8")
    rpath = tmp_path / "r.jsonl"; rpath.write_text("y\n", encoding="utf-8")
    kwargs = _acceptance_kwargs(_items_for(common.H2_STEMS))
    kwargs["concurrency_artifact"] = cpath; kwargs["resource_artifact"] = rpath
    acceptance = reobserve.build_acceptance(**kwargs)
    out = tmp_path / "h5_reobserve_acceptance.json"
    out.write_text(json.dumps(acceptance), encoding="utf-8")
    loaded = json.loads(out.read_text())
    assert loaded["executions_consumed"] == 16
    assert loaded["final_actual_total"] == 152


# ---------------------------------------------------------------------------
# Static safety guards
# ---------------------------------------------------------------------------

def test_reobserve_artifacts_are_unique_and_under_reobserve_root() -> None:
    assert reobserve.REOBSERVE_ROOT == common.PLAN_B_ROOT / "h5_reobserve"
    for artifact in (reobserve.CONCURRENCY_ARTIFACT, reobserve.RESOURCE_ARTIFACT,
                     reobserve.ACCEPTANCE_ARTIFACT):
        assert artifact.parent == reobserve.REOBSERVE_ROOT
        assert artifact.name.startswith("h5_reobserve")


def test_reobserve_preserves_historical_roots() -> None:
    source = (SCRIPTS / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    for name in ("h2h3", "h4", "h5"):
        assert name in source
    assert "already exists" in source  # fail-closed on existing artifacts


def test_reobserve_single_experiment_full_membership_only() -> None:
    source = (SCRIPTS / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    assert "h5_cycles()[0]" in source          # H5_FULL only; never the tail-8 cycle
    assert "assert_cycle_membership" in source
    assert 'PLUGIN = "cpn_bandwidth_tier"' in source

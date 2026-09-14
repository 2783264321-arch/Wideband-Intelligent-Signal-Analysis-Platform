"""Plan B H5: concurrency + endurance contract (CPU-only, no GPU)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_common as common  # noqa: E402
import plan_b_h5_core as h5  # noqa: E402


def test_h5_cycles_partition_40() -> None:
    cycles = h5.h5_cycles(common.H2_STEMS)
    assert [len(c) for c in cycles] == [16, 16, 8]
    assert h5.h5_expected_executions(common.H2_STEMS) == 40
    assert cycles[2] == list(common.H2_STEMS[:8])
    assert cycles[0] == cycles[1] == list(common.H2_STEMS)


def test_h5_requires_exactly_16_stems() -> None:
    import pytest

    with pytest.raises(ValueError):
        h5.h5_cycles(list(common.H2_STEMS)[:15])


def test_h5_concurrency_bound_check() -> None:
    ok1 = h5.evaluate_concurrency_sample(h5.ConcurrencySample(
        timestamp=0.0, worker_pids=(1,), worker_run_ids=(), gpu_compute_pids=(1,),
        db_running_runs=1, db_pending_runs=0, interval_s=0.5,
    ))
    assert ok1.abort is False
    ok2 = h5.evaluate_concurrency_sample(h5.ConcurrencySample(
        timestamp=0.0, worker_pids=(1, 2), worker_run_ids=(), gpu_compute_pids=(1, 2),
        db_running_runs=2, db_pending_runs=0, interval_s=0.5,
    ))
    assert ok2.abort is False
    bad = h5.evaluate_concurrency_sample(h5.ConcurrencySample(
        timestamp=0.0, worker_pids=(1, 2, 3), worker_run_ids=(), gpu_compute_pids=(1, 2, 3),
        db_running_runs=3, db_pending_runs=0, interval_s=0.5,
    ))
    assert bad.abort is True
    assert bad.reason == "CONCURRENCY_BOUND_EXCEEDED"


def test_h5_unmapped_gpu_pid_is_an_abort() -> None:
    evaluation = h5.evaluate_concurrency_sample(h5.ConcurrencySample(
        timestamp=0.0, worker_pids=(1,), worker_run_ids=(), gpu_compute_pids=(1, 99),
        db_running_runs=1, db_pending_runs=0, interval_s=0.5,
    ))
    assert evaluation.abort is True
    assert evaluation.reason == "UNMAPPED_GPU_COMPUTE_PID"


def test_h5_low_frequency_sample_is_recorded_as_violation() -> None:
    evaluation = h5.evaluate_concurrency_sample(h5.ConcurrencySample(
        timestamp=0.0, worker_pids=(1,), worker_run_ids=(), gpu_compute_pids=(1,),
        db_running_runs=1, db_pending_runs=0, interval_s=5.0,
    ))
    assert evaluation.checks["interval_high_frequency"] is False


def test_h5_max_observed_concurrency() -> None:
    samples = [
        h5.ConcurrencySample(0.0, (1,), (), (1,), 1, 0, 0.5),
        h5.ConcurrencySample(0.1, (1, 2), (), (1, 2), 2, 0, 0.5),
        h5.ConcurrencySample(0.2, (2,), (), (2,), 1, 0, 0.5),
    ]
    assert h5.max_observed_concurrency(samples) == 2


def test_h5_gpu_quiescence_rule() -> None:
    within = h5.evaluate_gpu_quiescence(
        baseline_mib=0, observed_mib=63, compute_apps=[], elapsed_since_terminal_s=10.0)
    assert within.abort is False

    over_margin_within_time = h5.evaluate_gpu_quiescence(
        baseline_mib=0, observed_mib=65, compute_apps=[], elapsed_since_terminal_s=10.0)
    assert over_margin_within_time.abort is False  # not yet timed out

    not_quiescent = h5.evaluate_gpu_quiescence(
        baseline_mib=0, observed_mib=65, compute_apps=[], elapsed_since_terminal_s=61.0)
    assert not_quiescent.abort is True
    assert not_quiescent.reason == "GPU_MEMORY_NOT_QUIESCENT"

    apps_present = h5.evaluate_gpu_quiescence(
        baseline_mib=0, observed_mib=0, compute_apps=[42], elapsed_since_terminal_s=1.0)
    assert apps_present.abort is True
    assert apps_present.reason == "GPU_MEMORY_NOT_QUIESCENT"


def test_h5_abort_conditions() -> None:
    resource_oom = h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=0, cgroup_events_oom_kill=1)
    evaluation = h5.evaluate_abort_conditions(
        samples=[], resource_samples=[resource_oom], resource_baseline={"oom_kill": 0},
        disk_free_bytes=None, unexpected_db_escape=False)
    assert evaluation.abort is True
    assert evaluation.reason == "CGROUP_MEMORY_EVENT"

    assert h5.evaluate_abort_conditions(
        samples=[], resource_samples=[], resource_baseline={}, disk_free_bytes=1 * 1024 ** 3,
        unexpected_db_escape=False).reason == "DISK_LOW"
    assert h5.evaluate_abort_conditions(
        samples=[], resource_samples=[], resource_baseline={}, disk_free_bytes=None,
        unexpected_db_escape=True).reason == "QUALIFICATION_DB_ESCAPE"
    assert h5.evaluate_abort_conditions(
        samples=[], resource_samples=[], resource_baseline={}, disk_free_bytes=None,
        unexpected_db_escape=False).abort is False


def test_h5_foreign_gpu_process_pre_admission() -> None:
    admitted, reason, foreign = h5.pre_admission_gate([(19076, 6494)], plan_b_pids=[])
    assert admitted is False
    assert reason == "FOREIGN_GPU_PROCESS_PRESENT"
    assert foreign == [(19076, 6494)]

    admitted2, reason2, _ = h5.pre_admission_gate([(111, 2048)], plan_b_pids=[111])
    assert admitted2 is True and reason2 is None

    admitted3, reason3, _ = h5.pre_admission_gate([], plan_b_pids=[])
    assert admitted3 is True and reason3 is None


def test_h5_sample_serialization(tmp_path: Path) -> None:
    samples = [
        h5.ConcurrencySample(0.0, (1,), ("run_a",), (1,), 1, 0, 0.5),
        h5.ConcurrencySample(0.5, (1, 2), ("run_a", "run_b"), (1, 2), 2, 0, 0.5),
    ]
    raw = h5.serialize_concurrency_samples(samples, tmp_path / "h5_concurrency_samples.jsonl")
    lines = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["worker_pids"] == [1]
    assert lines[1]["worker_pids"] == [1, 2]

    resources = [h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=10)]
    out = h5.serialize_resource_samples(resources, tmp_path / "h5_resources.json")
    assert json.loads(out.read_text(encoding="utf-8"))[0]["gpu_memory_used_mib"] == 10


def test_h5_evidence_path_and_constants() -> None:
    assert h5.CONCURRENCY_BOUND == 2
    assert h5.CONCURRENCY_SAMPLE_INTERVAL_S <= 0.5
    assert h5.GPU_QUIESCENT_MARGIN_MIB == 64
    assert h5.GPU_QUIESCENT_TIMEOUT_S == 60
    assert str(common.EVIDENCE_DIR).startswith(str(common.PLAN_B_ROOT))
    source = (SCRIPTS / "plan_b_h5_concurrency_endurance.py").read_text(encoding="utf-8")
    assert "run_per_campaign_admission" in source
    assert "16 + 16 + 8" in source or "h5_expected_executions" in source or "total_expected_executions" in source


def test_h5_high_frequency_gap_constants() -> None:
    assert h5.H5_TARGET_SAMPLE_PERIOD_S <= 0.25
    assert h5.H5_MAX_SAMPLE_GAP_S <= 0.5


def test_h5_sampling_gap_abort_is_enforced() -> None:
    # 0.51 s actual gap -> CONCURRENCY_SAMPLING_GAP
    over = h5.evaluate_concurrency_sample(h5.ConcurrencySample(
        timestamp=0.0, worker_pids=(1,), worker_run_ids=("run_a",), gpu_compute_pids=(1,),
        db_running_runs=1, db_pending_runs=0, interval_s=0.51,
    ))
    assert over.abort is True
    assert over.reason == "CONCURRENCY_SAMPLING_GAP"


def test_h5_three_qualification_workers_abort() -> None:
    evaluation = h5.evaluate_concurrency_sample(h5.ConcurrencySample(
        timestamp=0.0, worker_pids=(1, 2, 3), worker_run_ids=("a", "b", "c"),
        gpu_compute_pids=(1, 2, 3), db_running_runs=3, db_pending_runs=0, interval_s=0.25,
    ))
    assert evaluation.abort is True
    assert evaluation.reason == "CONCURRENCY_BOUND_EXCEEDED"


def test_h5_final_acceptance_requires_actual_db_40() -> None:
    def _cycle(n, runs, attempts, status="completed"):
        return {
            "status": status,
            "_membership": {"expected_items": n},
            "completed_items": n, "failed_items": 0,
            "_actual_run_count": runs, "_actual_attempt_count": attempts,
        }

    cycles = [_cycle(16, 16, 16), _cycle(16, 16, 16), _cycle(8, 8, 8)]
    samples = [
        h5.ConcurrencySample(0.0, (1,), ("a",), (1,), 1, 0, 0.25),
        h5.ConcurrencySample(0.25, (1, 2), ("a", "b"), (1, 2), 2, 0, 0.25),
    ]
    resources = [h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=0)]
    result = h5.evaluate_final_acceptance(
        cycle_summaries=cycles, concurrency_samples=samples, resource_samples=resources,
        resource_baseline={"max": 0, "oom": 0, "oom_kill": 0}, foreign_gpu_pids=[],
        concurrency_artifact_present=True, resource_artifact_present=True,
    )
    assert result["passed"] is True
    assert result["actual_runs"] == [16, 16, 8]
    assert result["actual_total_runs"] == 40

    # A cycle that claims 8 intended but actually ran 16 fails.
    bad = [_cycle(16, 16, 16), _cycle(16, 16, 16), {"status": "completed",
           "_membership": {"expected_items": 8}, "completed_items": 8, "failed_items": 0,
           "_actual_run_count": 16, "_actual_attempt_count": 16}]
    assert h5.evaluate_final_acceptance(
        cycle_summaries=bad, concurrency_samples=samples, resource_samples=resources,
        resource_baseline={"max": 0, "oom": 0, "oom_kill": 0}, foreign_gpu_pids=[],
        concurrency_artifact_present=True, resource_artifact_present=True,
    )["passed"] is False


def test_h5_final_acceptance_rejects_empty_monitor_evidence() -> None:
    cycles = [
        {"status": "completed", "_membership": {"expected_items": 16}, "completed_items": 16,
         "failed_items": 0, "_actual_run_count": 16, "_actual_attempt_count": 16},
        {"status": "completed", "_membership": {"expected_items": 16}, "completed_items": 16,
         "failed_items": 0, "_actual_run_count": 16, "_actual_attempt_count": 16},
        {"status": "completed", "_membership": {"expected_items": 8}, "completed_items": 8,
         "failed_items": 0, "_actual_run_count": 8, "_actual_attempt_count": 8},
    ]
    empty = h5.evaluate_final_acceptance(
        cycle_summaries=cycles, concurrency_samples=[], resource_samples=[],
        resource_baseline={"max": 0, "oom": 0, "oom_kill": 0}, foreign_gpu_pids=[],
        concurrency_artifact_present=False, resource_artifact_present=False,
    )
    assert empty["passed"] is False
    assert empty["checks"]["concurrency_samples_present"] is False
    assert empty["checks"]["all_gaps_le_max"] is False  # max([]) must not pass


def test_h5_final_acceptance_rejects_monitor_failure() -> None:
    cycles = [
        {"status": "completed", "_membership": {"expected_items": n}, "completed_items": n,
         "failed_items": 0, "_actual_run_count": n, "_actual_attempt_count": n}
        for n in (16, 16, 8)
    ]
    samples = [h5.ConcurrencySample(0.0, (1,), ("a",), (1,), 1, 0, 0.25)]
    resources = [h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=0)]
    failed = h5.evaluate_final_acceptance(
        cycle_summaries=cycles, concurrency_samples=samples, resource_samples=resources,
        resource_baseline={"max": 0, "oom": 0, "oom_kill": 0}, foreign_gpu_pids=[],
        concurrency_monitor_failure="RESOURCE_MONITOR_FAILURE: boom",
        resource_monitor_failure=None,
        concurrency_artifact_present=True, resource_artifact_present=True,
    )
    assert failed["passed"] is False
    assert failed["checks"]["concurrency_monitor_ok"] is False


def test_h5_cgroup_event_delta_nonzero_baseline() -> None:
    # historical baseline max=10, current max=10 -> delta 0 accepted
    sample_same = h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=0,
                                    cgroup_events_max=10, cgroup_events_oom=0, cgroup_events_oom_kill=0)
    ok = h5.evaluate_resource_sample(sample=sample_same, baseline={"max": 10, "oom": 0, "oom_kill": 0})
    assert ok.abort is False
    # current max=11 -> delta 1 abort
    sample_over = h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=0,
                                    cgroup_events_max=11, cgroup_events_oom=0, cgroup_events_oom_kill=0)
    bad = h5.evaluate_resource_sample(sample=sample_over, baseline={"max": 10, "oom": 0, "oom_kill": 0})
    assert bad.abort is True
    assert bad.reason == "CGROUP_MEMORY_EVENT"


def test_h5_resource_delta_baseline_semantics() -> None:
    assert h5.evaluate_resource_deltas(baseline={"max": 3, "oom": 0, "oom_kill": 0},
                                       current={"max": 3, "oom": 0, "oom_kill": 0}) == {
        "max": 0, "oom": 0, "oom_kill": 0, "high": 0}
    assert h5.evaluate_resource_deltas(baseline={"oom": 0}, current={"oom": 1})["oom"] == 1


def test_h5_campaign_script_uses_membership_and_monitor() -> None:
    source = (SCRIPTS / "plan_b_h5_concurrency_endurance.py").read_text(encoding="utf-8")
    assert "assert_cycle_membership" in source
    assert "ConcurrencyMonitor" in source
    assert "ResourceMonitor" in source
    assert "run_per_campaign_admission" in source


def test_h5_pre_admission_empty_allowlist_treats_all_as_foreign() -> None:
    # Issue 1: before the first H5 experiment, allowlist is empty; ANY compute PID is foreign.
    admitted, reason, foreign = h5.pre_admission_gate([(555, 4096)], plan_b_pids=())
    assert admitted is False
    assert reason == "FOREIGN_GPU_PROCESS_PRESENT"
    assert foreign == [(555, 4096)]
    # no compute process -> may continue
    ok, reason2, _ = h5.pre_admission_gate([], plan_b_pids=())
    assert ok is True and reason2 is None


def test_h5_campaign_passes_empty_allowlist() -> None:
    source = (SCRIPTS / "plan_b_h5_concurrency_endurance.py").read_text(encoding="utf-8")
    assert "run_per_campaign_admission(core, plan_b_pids=())" in source


def test_h5_cycle_acceptance_requires_real_read_models() -> None:
    class _Cycle:
        index = 0
        expected_items = 16

    good = {
        "status": "completed", "expected_items": 16, "completed_items": 16,
        "failed_items": 0, "queued_items": 0, "running_items": 0, "attempt_count": 16,
        "_items": [{"id": f"i{n}", "status": "completed"} for n in range(16)],
        "_attempt_counts": {f"i{n}": 1 for n in range(16)},
        "_actual_runs": [f"run_{n}" for n in range(16)],
        "_evaluation": {"status": "completed", "evaluated_recordings": 16,
                        "missing_recordings": 0, "coverage": 1.0},
    }
    assert h5.evaluate_cycle_acceptance(summary=good, cycle=_Cycle()).abort is False

    bad = dict(good)
    bad["_attempt_counts"] = {f"i{n}": 2 for n in range(16)}  # retries present
    assert h5.evaluate_cycle_acceptance(summary=bad, cycle=_Cycle()).abort is True

    bad2 = dict(good)
    bad2["_evaluation"] = {"status": "completed", "evaluated_recordings": 16,
                           "missing_recordings": 0, "coverage": 0.9}
    assert h5.evaluate_cycle_acceptance(summary=bad2, cycle=_Cycle()).abort is True


def test_h5_monitor_fails_closed_on_sampler_exception() -> None:
    from plan_b_h5_monitor import _MonitorBase

    class _Boom(_MonitorBase):
        failure_code = "CONCURRENCY_MONITOR_FAILURE"

        def _run(self):
            raise RuntimeError("sampler exploded")

    monitor = _Boom(evidence_path=Path("/tmp/opencode/h5_boom.jsonl"))
    monitor.start()
    for _ in range(100):
        if monitor.thread_failure is not None:
            break
        import time as _t
        _t.sleep(0.01)
    monitor.stop()
    assert monitor.thread_failure is not None
    assert monitor.abort_reason == "CONCURRENCY_MONITOR_FAILURE"


def test_h5_worker_pid_for_run_and_ownership(tmp_path: Path) -> None:
    import plan_b_h5_monitor as monitor

    (tmp_path / "11").mkdir()
    (tmp_path / "11" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_owned\0"
    )
    (tmp_path / "22").mkdir()
    (tmp_path / "22" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_other\0"
    )
    assert monitor.worker_pid_for_run("run_owned", proc_root=tmp_path) == 11
    assert monitor.worker_pid_for_run("run_missing", proc_root=tmp_path) is None
    pairs = monitor.map_ownership_pairs(["run_owned", "run_missing"], proc_root=tmp_path)
    assert [(p.run_id, p.pid) for p in pairs] == [("run_owned", 11), ("run_missing", None)]
    # unrelated worker (run_other) is NOT qualification-owned for run_owned
    assert monitor.worker_pid_for_run("run_owned", proc_root=tmp_path) != 22


def test_h5_ambiguous_worker_pid_fails_closed(tmp_path: Path) -> None:
    import plan_b_h5_monitor as monitor

    for name in ("11", "22"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "cmdline").write_bytes(
            b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_dup\0"
        )
    import pytest
    with pytest.raises(RuntimeError):
        monitor.worker_pid_for_run("run_dup", proc_root=tmp_path)


def test_h5_a1_memory_admission_is_invoked() -> None:
    import plan_b_h5_core as core_h5
    assert hasattr(core_h5, "run_a1_memory_admission")
    source = (SCRIPTS / "plan_b_h5_core.py").read_text(encoding="utf-8")
    assert "require_memory_admission" in source
    # admission result carries the exact A1 report
    assert "a1_admission" in source


def test_h5_resource_sample_has_real_endurance_fields() -> None:
    sample = h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=10)
    for field_name in (
        "gpu_memory_free_mib", "gpu_utilization_pct", "worker_rss_kb", "worker_vmhwm_kb",
        "cgroup_memory_current_bytes", "cgroup_clean_file_cache_bytes",
        "cgroup_committed_floor_bytes", "cgroup_effective_headroom_bytes",
        "cgroup_events_max", "cgroup_events_oom", "cgroup_events_oom_kill",
        "disk_free_bytes", "db_completed_items", "db_run_count",
    ):
        assert hasattr(sample, field_name), field_name

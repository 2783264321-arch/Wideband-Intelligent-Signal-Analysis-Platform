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
    resource_oom = h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=0, cgroup_oom_kill_events=1)
    evaluation = h5.evaluate_abort_conditions(
        samples=[], resource_samples=[resource_oom], disk_free_bytes=None, unexpected_db_escape=False)
    assert evaluation.abort is True
    assert evaluation.reason == "CGROUP_MEMORY_EVENT"

    assert h5.evaluate_abort_conditions(
        samples=[], resource_samples=[], disk_free_bytes=1 * 1024 ** 3,
        unexpected_db_escape=False).reason == "DISK_LOW"
    assert h5.evaluate_abort_conditions(
        samples=[], resource_samples=[], disk_free_bytes=None,
        unexpected_db_escape=True).reason == "QUALIFICATION_DB_ESCAPE"
    assert h5.evaluate_abort_conditions(
        samples=[], resource_samples=[], disk_free_bytes=None,
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
    assert "pre_admission_gate" in source
    assert "16 + 16 + 8" in source or "h5_expected_executions" in source

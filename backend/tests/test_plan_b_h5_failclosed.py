"""Plan B H5 fail-closed telemetry + drain + exactly-once tests (CPU-only).

Covers the final corrective issues:
  * nvidia-smi fail-closed parsing (no synthetic zeros on failure)
  * exact worker drain timeout (fail closed)
  * mandatory cgroup baseline
  * H5 fresh-state exactly-once guard
  * ResourceMonitor exact active-experiment ownership + live A1 abort
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_dataset_core as core  # noqa: E402
import plan_b_h5_core as h5  # noqa: E402
import plan_b_h5_monitor as monitor  # noqa: E402


def _completed(stdout, returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _runner(mapping):
    def run(argv, **kwargs):
        key = tuple(argv[1:])
        return mapping.get(key, _completed("", returncode=1))
    return run


# --- Issue 4: nvidia-smi fail closed -----------------------------------------

def test_nvidia_smi_nonzero_fails_closed() -> None:
    runner = _runner({})
    with pytest.raises(core.NvidiaSmiError):
        core.compute_apps(runner=runner)
    with pytest.raises(core.NvidiaSmiError):
        core.gpu_memory_used_mib(runner=runner)
    with pytest.raises(core.NvidiaSmiError):
        core.gpu_metrics(runner=runner)


def test_nvidia_smi_empty_output_fails_closed() -> None:
    # For scalar GPU queries, empty stdout is NOT a legitimate result.
    runner = _runner({("--query-gpu=memory.used", "--format=csv,noheader,nounits"):
                      _completed("   \n")})
    with pytest.raises(core.NvidiaSmiError):
        core.gpu_memory_used_mib(runner=runner)


def test_nvidia_smi_malformed_fails_closed() -> None:
    runner = _runner({("--query-gpu=memory.used", "--format=csv,noheader,nounits"):
                      _completed("not_a_number\n")})
    with pytest.raises(core.NvidiaSmiError):
        core.gpu_memory_used_mib(runner=runner)

    bad_apps = _runner({("--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"):
                        _completed("abc,def\n")})
    with pytest.raises(core.NvidiaSmiError):
        core.compute_apps(runner=bad_apps)


def test_nvidia_smi_valid_no_compute_is_genuinely_empty() -> None:
    # rc 0 + empty stdout is the legitimate "no compute processes" state.
    runner = _runner({("--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"):
                      _completed("")})
    assert core.compute_apps(runner=runner) == []


def test_nvidia_smi_valid_metrics_parsed() -> None:
    runner = _runner({
        ("--query-gpu=memory.used,memory.free,utilization.gpu", "--format=csv,noheader,nounits"):
            _completed("12, 34000, 7\n"),
        ("--query-gpu=memory.used", "--format=csv,noheader,nounits"): _completed("12\n"),
    })
    assert core.gpu_metrics(runner=runner) == {"used_mib": 12, "free_mib": 34000, "utilization_pct": 7}
    assert core.gpu_memory_used_mib(runner=runner) == 12


def test_nvidia_smi_valid_compute_rows_parsed() -> None:
    runner = _runner({("--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"):
                      _completed("111, 2048\n222, 4096\n")})
    assert core.compute_apps(runner=runner) == [(111, 2048), (222, 4096)]


# --- Issue 9: mandatory cgroup baseline --------------------------------------

def test_cgroup_baseline_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(h5.CgroupBaselineError):
        h5.read_cgroup_events_baseline(events_path=tmp_path / "missing.events")


def test_cgroup_baseline_missing_keys_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "memory.events"
    path.write_text("max 10\n")  # oom/oom_kill absent
    with pytest.raises(h5.CgroupBaselineError):
        h5.read_cgroup_events_baseline(events_path=path)


def test_cgroup_baseline_valid_returns_mandatory_keys(tmp_path: Path) -> None:
    path = tmp_path / "memory.events"
    path.write_text("low 0\nmax 10\noom 7\noom_kill 2\n")
    assert h5.read_cgroup_events_baseline(events_path=path) == {"max": 10, "oom": 7, "oom_kill": 2}


# --- Issue 3: oom_kill baseline typo -----------------------------------------

def test_cgroup_event_delta_distinguishes_oom_and_oom_kill() -> None:
    baseline = {"max": 10, "oom": 7, "oom_kill": 2}
    # Same current -> all deltas zero even though oom != oom_kill.
    same = h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=0,
                             cgroup_events_max=10, cgroup_events_oom=7, cgroup_events_oom_kill=2)
    result = h5.evaluate_resource_sample(sample=same, baseline=baseline)
    assert result.abort is False
    # oom_kill grows by 1 -> abort (old bug subtracted oom baseline=7).
    grown = h5.ResourceSample(timestamp=0.0, gpu_memory_used_mib=0,
                              cgroup_events_max=10, cgroup_events_oom=7, cgroup_events_oom_kill=3)
    bad = h5.evaluate_resource_sample(sample=grown, baseline=baseline)
    assert bad.abort is True
    assert bad.reason == "CGROUP_MEMORY_EVENT"


# --- Issue 8: exact worker drain ---------------------------------------------

def test_exact_worker_drain_timeout_fails_closed(tmp_path: Path) -> None:
    # A live exact worker for run_drain exists -> drain must time out (bounded).
    (tmp_path / "11").mkdir()
    (tmp_path / "11" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_drain\0"
    )
    clock = _Seq([0.0, 0.5, 1.1, 2.0])
    with pytest.raises(core.WorkerDrainTimeout):
        core.wait_for_exact_workers_drained(
            ["run_drain"], proc_root=tmp_path, timeout_s=1, poll_s=0.1,
            clock=clock, sleep=lambda _s: None,
        )


def test_exact_worker_drain_returns_when_gone(tmp_path: Path) -> None:
    core.wait_for_exact_workers_drained(
        ["run_absent"], proc_root=tmp_path, timeout_s=1, poll_s=0.01,
    )


# --- Issue 5: fresh-state exactly-once guard ---------------------------------

def test_fresh_state_allows_when_db_absent(tmp_path: Path) -> None:
    assert h5.check_h5_fresh_state(tmp_path / "qual.db")["fresh"] is True


def test_fresh_state_blocks_existing_experiment(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "qual.db"
    connection = sqlite3.connect(str(db))
    connection.execute("create table dataset_experiments (id text)")
    connection.execute("create table dataset_experiment_attempts (id text)")
    connection.execute("create table analysis_runs (id text)")
    connection.execute("insert into dataset_experiments values ('exp_1')")
    connection.commit()
    connection.close()
    state = h5.check_h5_fresh_state(db)
    assert state["fresh"] is False
    assert state["reason"] == "H5_EXISTING_STATE"


def test_fresh_state_blocks_existing_run_without_experiment(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "qual.db"
    connection = sqlite3.connect(str(db))
    connection.execute("create table dataset_experiments (id text)")
    connection.execute("create table dataset_experiment_attempts (id text)")
    connection.execute("create table analysis_runs (id text)")
    connection.execute("insert into analysis_runs values ('run_orphan')")
    connection.commit()
    connection.close()
    assert h5.check_h5_fresh_state(db)["fresh"] is False


# --- Issue 1: ResourceMonitor exact active-experiment ownership --------------

def test_resource_monitor_uses_exact_active_experiment_workers(tmp_path: Path) -> None:
    (tmp_path / "11").mkdir()
    (tmp_path / "11" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_owned\0"
    )
    (tmp_path / "11" / "status").write_text(
        "Name:\tpython\nVmRSS:\t12345 kB\nVmHWM:\t23456 kB\n"
    )
    (tmp_path / "22").mkdir()
    (tmp_path / "22" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_other\0"
    )

    captured = {}

    def _gpu():
        return {"used_mib": 0, "free_mib": 100, "utilization_pct": 0}

    def _run_ids(_experiment_id):
        return ["run_owned"] if _experiment_id == "exp_active" else []

    sample_maker = monitor.ResourceMonitor(
        evidence_path=tmp_path / "r.jsonl",
        interval_s=5.0,
        gpu_metrics=_gpu,
        run_ids_for_experiment=_run_ids,
        raw_snapshot_reader=lambda: {},
        db_counts=lambda: {},
        proc_root=tmp_path,
    )
    sample_maker.activate("exp_active")
    sample = sample_maker._sample()
    # The exact owned worker (PID 11) is sampled; unrelated PID 22 is excluded.
    assert list(sample.worker_vmhwm_kb.keys()) == [11]
    assert sample_maker._exact_worker_pids() == [11]
    sample_maker.deactivate()
    assert sample_maker._exact_worker_pids() == []


class _Seq:
    def __init__(self, values):
        self._values = list(values)
        self._last = values[-1]

    def __call__(self):
        if self._values:
            self._last = self._values.pop(0)
        return self._last


def test_unmapped_gpu_pid_transient_does_not_abort_immediately() -> None:
    """A single racing sample (GPU PID before ownership mapping) must NOT abort;
    a persistent unmapped PID still aborts after the grace window."""
    monitor_obj = monitor.ConcurrencyMonitor(
        app=None,
        evidence_path=Path("/tmp/opencode/h5_race.jsonl"),
        target_period_s=0.25, max_gap_s=0.5, bound=2,
        run_ids_for_experiment=lambda _eid: [],
        gpu_compute_pids=lambda: [],
    )
    # Sample 1: transient unmapped PID -> tolerated.
    s1 = h5.ConcurrencySample(0.0, (), (), (65011,), 0, 0, 0.25, experiment_id="e")
    monitor_obj._evaluate(s1)
    assert monitor_obj.abort_reason is None
    # Sample 2: PID became mapped -> streak resets.
    s2 = h5.ConcurrencySample(0.25, (65011,), ("run_x",), (65011,), 1, 0, 0.25,
                              experiment_id="e")
    monitor_obj._evaluate(s2)
    assert monitor_obj.abort_reason is None
    # Samples 3..N: persistently unmapped -> abort after grace window.
    for _ in range(monitor_obj._unmapped_grace_samples):
        monitor_obj._evaluate(h5.ConcurrencySample(0.5, (), (), (65099,), 0, 0, 0.25,
                                                   experiment_id="e"))
    assert monitor_obj.abort_reason == "UNMAPPED_GPU_COMPUTE_PID"


def test_concurrency_bound_exceeded_aborts_immediately() -> None:
    monitor_obj = monitor.ConcurrencyMonitor(
        app=None,
        evidence_path=Path("/tmp/opencode/h5_bound.jsonl"),
        target_period_s=0.25, max_gap_s=0.5, bound=2,
        run_ids_for_experiment=lambda _eid: [],
        gpu_compute_pids=lambda: [],
    )
    monitor_obj._evaluate(h5.ConcurrencySample(
        0.0, (1, 2, 3), ("a", "b", "c"), (1, 2, 3), 3, 0, 0.25, experiment_id="e"))
    assert monitor_obj.abort_reason == "CONCURRENCY_BOUND_EXCEEDED"


def test_sampling_gap_aborts_immediately() -> None:
    monitor_obj = monitor.ConcurrencyMonitor(
        app=None,
        evidence_path=Path("/tmp/opencode/h5_gap.jsonl"),
        target_period_s=0.25, max_gap_s=0.5, bound=2,
        run_ids_for_experiment=lambda _eid: [],
        gpu_compute_pids=lambda: [],
    )
    monitor_obj._evaluate(h5.ConcurrencySample(
        0.0, (1,), ("a",), (1,), 1, 0, 0.7, experiment_id="e"))
    assert monitor_obj.abort_reason == "CONCURRENCY_SAMPLING_GAP"


def test_h5_resume_rejects_fresh_db() -> None:
    source = (SCRIPTS / "plan_b_h5_concurrency_endurance.py").read_text(encoding="utf-8")
    assert "--start-cycle" in source
    assert "verify_prior_cycles" in source
    assert 'if fresh["fresh"] and start_cycle != 0' in source
    assert 'if not fresh["fresh"] and start_cycle == 0' in source

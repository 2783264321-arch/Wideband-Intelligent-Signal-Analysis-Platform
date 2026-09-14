"""Plan B H4: running-worker crash targeting contract (deterministic, no GPU).

Proves the H4 live-crash selector can only return a kill target when the
AnalysisRun itself is ``running`` (not merely the DatasetExperimentItem), so a
future live kill exercises a genuine running-worker crash — never the
launch-ambiguous pending window.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_h4_targeting as targeting  # noqa: E402


def _constant(value):
    return lambda: value


def test_item_running_run_pending_is_not_a_target() -> None:
    with pytest.raises(TimeoutError):
        targeting.wait_for_verified_running_worker(
            experiment_id="exp_1",
            list_items=_constant([{"id": "item_1", "status": "running",
                                   "latest_analysis_run_id": "run_1"}]),
            get_run=lambda run_id: {"id": run_id, "status": "pending"},
            pid_for_run=lambda run_id: 4242,
            gpu_compute_pids=_constant([4242]),
            deadline_s=1,
            poll_s=0.01,
            sleep=lambda _s: None,
            now=_now_sequence([0.0, 0.5, 1.5]),
        )


def test_item_running_run_running_wrong_pid_is_not_a_target() -> None:
    with pytest.raises(TimeoutError):
        targeting.wait_for_verified_running_worker(
            experiment_id="exp_1",
            list_items=_constant([{"id": "item_1", "status": "running",
                                   "latest_analysis_run_id": "run_1"}]),
            get_run=lambda run_id: {"id": run_id, "status": "running"},
            pid_for_run=lambda run_id: None,  # cmdline identity not verified
            gpu_compute_pids=_constant([]),
            deadline_s=1,
            poll_s=0.01,
            sleep=lambda _s: None,
            now=_now_sequence([0.0, 0.5, 1.5]),
        )


def test_item_running_run_running_verified_pid_is_a_target() -> None:
    target = targeting.wait_for_verified_running_worker(
        experiment_id="exp_1",
        list_items=_constant([{"id": "item_1", "status": "running",
                               "latest_analysis_run_id": "run_1"}]),
        get_run=lambda run_id: {"id": run_id, "status": "running"},
        pid_for_run=lambda run_id: 4242,
        gpu_compute_pids=_constant([4242]),
        deadline_s=5,
        poll_s=0.01,
        sleep=lambda _s: None,
        now=_now_sequence([0.0, 0.1]),
    )
    assert target.pre_kill_status == "running"
    assert target.run_id == "run_1"
    assert target.item_id == "item_1"
    assert target.pid == 4242
    assert target.verified_cmdline is True
    assert target.gpu_compute_pid_seen is True


def test_target_records_gpu_pid_absent() -> None:
    target = targeting.wait_for_verified_running_worker(
        experiment_id="exp_1",
        list_items=_constant([{"id": "item_1", "status": "running",
                               "latest_analysis_run_id": "run_1"}]),
        get_run=lambda run_id: {"id": run_id, "status": "running"},
        pid_for_run=lambda run_id: 99,
        gpu_compute_pids=_constant([1234]),  # a different PID
        deadline_s=5,
        poll_s=0.01,
        sleep=lambda _s: None,
        now=_now_sequence([0.0, 0.1]),
    )
    assert target.gpu_compute_pid_seen is False
    assert target.pre_kill_status == "running"


def test_completed_run_is_never_a_target() -> None:
    with pytest.raises(TimeoutError):
        targeting.wait_for_verified_running_worker(
            experiment_id="exp_1",
            list_items=_constant([{"id": "item_1", "status": "running",
                                   "latest_analysis_run_id": "run_1"}]),
            get_run=lambda run_id: {"id": run_id, "status": "completed"},
            pid_for_run=lambda run_id: 4242,
            gpu_compute_pids=_constant([4242]),
            deadline_s=1,
            poll_s=0.01,
            sleep=lambda _s: None,
            now=_now_sequence([0.0, 0.5, 1.5]),
        )


def test_verified_worker_pid_matches_exact_cmdline(tmp_path: Path) -> None:
    proc = tmp_path
    (proc / "111").mkdir()
    (proc / "111" / "cmdline").write_bytes(b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_abc\0")
    (proc / "222").mkdir()
    (proc / "222" / "cmdline").write_bytes(b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_other\0")
    (proc / "333").mkdir()
    (proc / "333" / "cmdline").write_bytes(b"/ml/python\0-m\0app.dataset_experiments.worker\0run_abc\0")
    assert targeting.verified_worker_pid("run_abc", proc_root=proc) == 111
    assert targeting.verified_worker_pid("run_missing", proc_root=proc) is None


class _Seq:
    def __init__(self, values):
        self._values = list(values)
        self._last = values[-1]

    def __call__(self):
        if self._values:
            self._last = self._values.pop(0)
        return self._last


def _now_sequence(values):
    return _Seq(values)

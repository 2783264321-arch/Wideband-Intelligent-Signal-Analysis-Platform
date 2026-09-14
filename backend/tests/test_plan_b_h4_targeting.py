"""Plan B H4: genuine GPU running-worker crash targeting contract (CPU-only).

Proves the H4 live-crash selector can only return a kill target when:
  * the DatasetExperimentItem and AnalysisRun are both running, AND
  * the exact cmdline-verified worker PID is present in the GPU compute-app set.

Also proves exact PID identity, trailing-arg rejection, and fail-closed
ambiguity when two PIDs claim the same run_id.
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


class _Seq:
    def __init__(self, values):
        self._values = list(values)
        self._last = values[-1]

    def __call__(self):
        if self._values:
            self._last = self._values.pop(0)
        return self._last


def _items(status="running", run_id="run_1"):
    return _constant([{"id": "item_1", "status": status, "latest_analysis_run_id": run_id}])


def test_run_running_exact_pid_gpu_absent_is_not_a_target() -> None:
    with pytest.raises(TimeoutError):
        targeting.wait_for_verified_running_worker(
            experiment_id="exp_1",
            list_items=_items(),
            get_run=lambda run_id: {"id": run_id, "status": "running"},
            pid_for_run=lambda run_id: 4242,
            gpu_compute_pids=_constant([9999]),  # PID not on GPU yet
            deadline_s=1,
            poll_s=0.01,
            sleep=lambda _s: None,
            now=_Seq([0.0, 0.3, 1.5]),
        )


def test_run_running_exact_pid_gpu_present_is_a_target() -> None:
    target = targeting.wait_for_verified_running_worker(
        experiment_id="exp_1",
        list_items=_items(),
        get_run=lambda run_id: {"id": run_id, "status": "running"},
        pid_for_run=lambda run_id: 4242,
        gpu_compute_pids=_constant([4242]),
        deadline_s=5,
        poll_s=0.01,
        sleep=lambda _s: None,
        now=_Seq([0.0, 0.1]),
    )
    assert target.pre_kill_status == "running"
    assert target.run_id == "run_1"
    assert target.item_id == "item_1"
    assert target.pid == 4242
    assert target.verified_cmdline is True
    assert target.gpu_compute_pid_seen is True


def test_run_pending_with_gpu_pid_is_not_a_target() -> None:
    with pytest.raises(TimeoutError):
        targeting.wait_for_verified_running_worker(
            experiment_id="exp_1",
            list_items=_items(),
            get_run=lambda run_id: {"id": run_id, "status": "pending"},
            pid_for_run=lambda run_id: 4242,
            gpu_compute_pids=_constant([4242]),  # GPU PID present but run not running
            deadline_s=1,
            poll_s=0.01,
            sleep=lambda _s: None,
            now=_Seq([0.0, 0.5, 1.5]),
        )


def test_verified_pid_rejects_trailing_unexpected_args(tmp_path: Path) -> None:
    (tmp_path / "111").mkdir()
    (tmp_path / "111" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_abc\0--extra\0"
    )
    (tmp_path / "222").mkdir()
    (tmp_path / "222" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_abc\0"
    )
    assert targeting.verified_worker_pid("run_abc", proc_root=tmp_path) == 222


def test_two_exact_pids_for_same_run_fail_closed(tmp_path: Path) -> None:
    for name in ("111", "222"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "cmdline").write_bytes(
            b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_abc\0"
        )
    with pytest.raises(targeting.AmbiguousWorkerError):
        targeting.verified_worker_pid("run_abc", proc_root=tmp_path)

    # The waiter propagates the ambiguity instead of choosing a PID.
    def _pid_for_run(run_id):
        return targeting.verified_worker_pid(run_id, proc_root=tmp_path)

    with pytest.raises(targeting.AmbiguousWorkerError):
        targeting.wait_for_verified_running_worker(
            experiment_id="exp_1",
            list_items=_items(run_id="run_abc"),
            get_run=lambda run_id: {"id": run_id, "status": "running"},
            pid_for_run=_pid_for_run,
            gpu_compute_pids=_constant([111, 222]),
            deadline_s=1,
            poll_s=0.01,
            sleep=lambda _s: None,
            now=_Seq([0.0, 0.5, 1.5]),
        )


def test_worker_pids_exact_cmdline_and_non_matches(tmp_path: Path) -> None:
    (tmp_path / "111").mkdir()
    (tmp_path / "111" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_abc\0"
    )
    (tmp_path / "333").mkdir()
    (tmp_path / "333" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.dataset_experiments.worker\0run_abc\0"
    )
    (tmp_path / "444").mkdir()
    (tmp_path / "444" / "cmdline").write_bytes(
        b"/ml/python\0-m\0app.analysis.local_inference_worker\0run_other\0"
    )
    assert targeting.verified_worker_pids("run_abc", proc_root=tmp_path) == [111]
    assert targeting.verified_worker_pid("run_missing", proc_root=tmp_path) is None

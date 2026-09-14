"""Plan B H4 — genuine local_gpu crash/recovery (real GPU, bounded 2–4 executions).

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
        scripts/plan_b_h4_recovery.py --live

Only kills a VERIFIED Plan-B worker PID (`/proc/<pid>/cmdline` contains
`app.analysis.local_inference_worker <exact run_id>`) whose AnalysisRun is
ALREADY ``status == running`` (enforced by `plan_b_h4_targeting`). The old
`item.status == running` wait was insufficient because the item transitions
before the run, which silently exercised the launch-ambiguous path instead of a
genuine running-worker crash. Never signals OpenCode, Jupyter, the interactive
backend, or unrelated processes.

This round is CPU-only preparation: do NOT run with --live while a foreign GPU
compute process occupies the device (main() refuses via the pre-admission gate).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402
import plan_b_dataset_core as core  # noqa: E402
import plan_b_h4_targeting as targeting  # noqa: E402

PLUGIN = "cpn_bandwidth_tier"


def _restart_and_recover(root: Path) -> dict:
    # A fresh app against the SAME DB runs startup recovery.
    app, _settings = core.build_app(root)
    return app


def live_crash_case(root: Path, label: str) -> dict:
    """Kill a worker whose AnalysisRun is ALREADY ``running`` AND whose PID is
    actively holding the GPU (genuine GPU crash).

    Selection is delegated to ``plan_b_h4_targeting`` which:
      * refuses to return a target while the run is still ``pending``;
      * requires the exact cmdline-verified PID;
      * requires that PID to be present in the GPU compute-app set.
    A ``run.status == "running"`` alone is insufficient because the worker sets
    it before loading the runtime and beginning real model execution.
    """
    from fastapi.testclient import TestClient

    app, _settings = core.build_app(root)
    core.register_subset(app)
    experiment_id = core.create_experiment(app, plugin_id=PLUGIN, name=f"Plan B H4 live {label}")
    client = TestClient(app)
    client.post(f"/api/dataset-experiments/{experiment_id}/run")

    def _list_items() -> list[dict]:
        return client.get(f"/api/dataset-experiments/{experiment_id}/items").json()

    def _get_run(run_id: str) -> dict:
        return client.get(f"/api/analysis-runs/{run_id}").json()

    target = targeting.wait_for_verified_running_worker(
        experiment_id=experiment_id,
        list_items=_list_items,
        get_run=_get_run,
        pid_for_run=lambda run_id: targeting.verified_worker_pid(run_id),
        gpu_compute_pids=lambda: [pid for pid, _mib in core.compute_apps()],
        deadline_s=600,
    )
    run_id = target.run_id
    pid = target.pid
    pre_status = target.pre_kill_status
    if pre_status != "running":
        raise SystemExit(f"H4 live: refusing to kill; pre_kill run status is {pre_status!r}")
    if not target.gpu_compute_pid_seen:
        raise SystemExit("H4 live: refusing to kill; GPU compute PID was not observed")
    os.kill(pid, signal.SIGKILL)
    # The app is restarted against the same DB; startup recovery must terminalize
    # the running local_gpu run and never rerun completed work.
    app2 = _restart_and_recover(root)
    client2 = TestClient(app2)
    deadline = time.time() + 300
    while time.time() < deadline:
        body = client2.get(f"/api/dataset-experiments/{experiment_id}").json()
        if body["status"] in ("completed", "completed_with_failures", "failed"):
            break
        time.sleep(1.0)
    run_after = client2.get(f"/api/analysis-runs/{run_id}").json()
    return {
        "label": label,
        "experiment_id": experiment_id,
        "item_id": target.item_id,
        "killed_run_id": run_id,
        "killed_pid": pid,
        "pre_kill_status": pre_status,
        "gpu_compute_pid_seen": target.gpu_compute_pid_seen,
        "post_restart_run_status": run_after["status"],
        "post_restart_run_error_type": run_after.get("error_type"),
        "experiment_status": client2.get(f"/api/dataset-experiments/{experiment_id}").json()["status"],
        "verified_cmdline": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required")

    root = common.PLAN_B_ROOT / "h4"
    baseline = core.gpu_memory_used_mib()
    if core.compute_apps():
        raise SystemExit(f"H4 STOP: GPU compute apps present: {core.compute_apps()}")

    results = []
    for label in ("case_a_startup", "case_b_mid_item"):
        results.append(live_crash_case(root, label))

    core.assert_no_orphans("H4 live post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H4")
    evidence = {"milestone": "H4", "live_cases": results, "gpu_quiescence": quiescent}
    path = core.write_evidence("h4_recovery_live.json", evidence)
    print(json.dumps({"live_cases": len(results), "evidence": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

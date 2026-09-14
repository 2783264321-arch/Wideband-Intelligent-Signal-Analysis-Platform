"""Plan B H5 — concurrency=2 + endurance 40 (real GPU campaign).

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
        scripts/plan_b_h5_concurrency_endurance.py --run

Workload: CPN golden, local_gpu, max_concurrency=2, 16 + 16 + 8 = 40 executions
across three sequential DatasetExperiments over explicit acceptance-only dataset
views (H5_FULL = frozen 16 stems; H5_TAIL8 = first 8), in a dedicated Plan-B DB.

DO NOT run while a foreign GPU compute process exists (pre-admission gate).

This round is CPU-only preparation: the campaign is NOT launched here.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402
import plan_b_dataset_core as core  # noqa: E402
import plan_b_h5_core as h5  # noqa: E402
import plan_b_h5_membership as membership  # noqa: E402
import plan_b_h5_monitor as monitor  # noqa: E402

PLUGIN = "cpn_bandwidth_tier"
CPN_MANIFEST = "7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb"


def _plan_b_worker_pids() -> list[int]:
    return monitor.plan_b_local_worker_pids()


def create_cycle_experiment(app, *, cycle: membership.H5Cycle, name: str, concurrency: int) -> str:
    """Create a DatasetExperiment bound to the cycle's EXACT dataset view."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    payload = {
        "name": name,
        "dataset_name": cycle.dataset_name,
        "dataset_split": membership.H5_SPLIT,
        "dataset_label_space": membership.H5_LABEL_SPACE,
        "plugin_id": PLUGIN,
        "plugin_version": common.PLUGIN_VERSION[PLUGIN],
        "model_release_id": common.MODEL_RELEASE,
        "executor": "local_gpu",
        "parameters": {},
        "max_concurrency": concurrency,
    }
    created = client.post("/api/dataset-experiments", json=payload)
    if created.status_code != 201:
        raise SystemExit(f"H5 STOP: create -> {created.status_code} {created.text}")
    return created.json()["id"]


def run_cycle(app, *, cycle: membership.H5Cycle, name: str, concurrency: int,
              monitor_thread: monitor.ConcurrencyMonitor, samples_path: Path,
              abort_holder: dict) -> dict:
    from fastapi.testclient import TestClient

    # Membership must be exact BEFORE the experiment is created/run.
    membership_report = membership.assert_cycle_membership(app, cycle)
    experiment_id = create_cycle_experiment(app, cycle=cycle, name=name, concurrency=concurrency)
    client = TestClient(app)
    monitor_thread.activate(experiment_id)
    client.post(f"/api/dataset-experiments/{experiment_id}/run")

    deadline = time.time() + 3600
    while time.time() < deadline:
        body = client.get(f"/api/dataset-experiments/{experiment_id}").json()
        if monitor_thread.abort_reason is not None:
            abort_holder["reason"] = monitor_thread.abort_reason
            raise SystemExit(f"H5 STOP: {monitor_thread.abort_reason}")
        if body["status"] in ("completed", "completed_with_failures", "failed"):
            monitor_thread.deactivate()
            body["_membership"] = membership_report
            body["_experiment_id"] = experiment_id
            return body
        time.sleep(0.5)
    monitor_thread.deactivate()
    raise SystemExit(f"H5 STOP: cycle {cycle.index} did not reach terminal state")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required (real 40-execution campaign)")

    root = common.PLAN_B_ROOT / "h5"
    admission = h5.run_per_campaign_admission(core, plan_b_pids=_plan_b_worker_pids())
    if not admission["admitted"]:
        raise SystemExit(f"H5 STOP: {admission['reason']}: {admission['detail']}")

    baseline = admission["gpu_memory_baseline_mib"]
    app, _settings = core.build_app(root)
    view_counts = {
        membership.H5_FULL: membership.register_dataset_view(
            app, dataset_name=membership.H5_FULL, stems=tuple(common.H2_STEMS)
        ),
        membership.H5_TAIL8: membership.register_dataset_view(
            app, dataset_name=membership.H5_TAIL8, stems=tuple(common.H2_STEMS[:8])
        ),
    }

    cycles = membership.h5_cycles()
    if membership.total_expected_executions() != 40:
        raise SystemExit("H5 STOP: cycle partition is not 40")

    samples_path = common.EVIDENCE_DIR / "h5_concurrency_samples.jsonl"
    monitor_thread = monitor.ConcurrencyMonitor(
        app=app,
        evidence_path=samples_path,
        target_period_s=h5.H5_TARGET_SAMPLE_PERIOD_S,
        max_gap_s=h5.H5_MAX_SAMPLE_GAP_S,
        bound=h5.CONCURRENCY_BOUND,
        worker_pids=monitor.plan_b_local_worker_pids,
        gpu_compute_pids=lambda: [pid for pid, _mib in core.compute_apps()],
    )
    monitor_thread.start()

    resource_path = common.EVIDENCE_DIR / "h5_resource_samples.jsonl"
    resource_monitor = monitor.ResourceMonitor(
        evidence_path=resource_path,
        gpu_memory_used_mib=core.gpu_memory_used_mib,
        plan_b_pids=monitor.plan_b_local_worker_pids,
        interval_s=h5.ENDURANCE_SAMPLE_INTERVAL_S,
    )
    resource_monitor.start()

    abort_holder: dict = {}
    cycle_summaries = []
    try:
        for index, cycle in enumerate(cycles):
            summary = run_cycle(
                app, cycle=cycle, name=f"Plan B H5 cycle {index + 1}", concurrency=h5.CONCURRENCY_BOUND,
                monitor_thread=monitor_thread, samples_path=samples_path, abort_holder=abort_holder,
            )
            cycle_summaries.append(summary)
    finally:
        monitor_thread.stop()
        resource_monitor.stop()

    core.assert_no_orphans("H5 post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H5")

    acceptance = h5.evaluate_final_acceptance(
        cycle_summaries=cycle_summaries,
        concurrency_samples=monitor_thread.samples,
        resource_samples=resource_monitor.samples,
        resource_baseline=admission["cgroup_events_baseline"],
        foreign_gpu_pids=monitor.foreign_compute_pids(monitor_thread.samples),
    )

    evidence = {
        "milestone": "H5",
        "experiment_ids": [s["_experiment_id"] for s in cycle_summaries],
        "view_counts": view_counts,
        "cycles": [c.expected_items for c in cycles],
        "expected_executions": membership.total_expected_executions(),
        "acceptance": acceptance,
        "gpu_quiescence": quiescent,
        "concurrency_samples": len(monitor_thread.samples),
        "resource_samples": len(resource_monitor.samples),
        "raw_concurrency_artifact": str(samples_path),
        "raw_resource_artifact": str(resource_path),
    }
    path = core.write_evidence("h5_concurrency_endurance.json", evidence)
    print(json.dumps({"experiment_ids": evidence["experiment_ids"],
                      "acceptance": acceptance, "evidence": str(path)}, indent=2))
    return 0 if acceptance["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

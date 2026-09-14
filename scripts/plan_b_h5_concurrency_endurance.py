"""Plan B H5 — concurrency=2 + endurance 40 (real GPU campaign).

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
        scripts/plan_b_h5_concurrency_endurance.py --run

Workload: CPN golden, local_gpu, max_concurrency=2, 16 + 16 + 8 = 40 executions
across three sequential DatasetExperiments over explicit acceptance-only dataset
views (H5_FULL = frozen 16 stems; H5_TAIL8 = first 8), in a dedicated Plan-B DB.

Admission refuses to start while a foreign GPU compute process exists (allowed
Plan-B set is EMPTY before the first H5 experiment).

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


def _run_ids_for_experiment(app, experiment_id: str) -> list[str]:
    from fastapi.testclient import TestClient

    items = TestClient(app).get(f"/api/dataset-experiments/{experiment_id}/items").json()
    return [i["latest_analysis_run_id"] for i in items if i.get("latest_analysis_run_id")]


def create_cycle_experiment(app, *, cycle: membership.H5Cycle, name: str, concurrency: int) -> str:
    from fastapi.testclient import TestClient

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
    created = TestClient(app).post("/api/dataset-experiments", json=payload)
    if created.status_code != 201:
        raise SystemExit(f"H5 STOP: create -> {created.status_code} {created.text}")
    return created.json()["id"]


def gather_cycle_evidence(app, *, experiment_id: str, cycle: membership.H5Cycle) -> dict:
    """Collect the real DB/API read models needed for per-cycle + final acceptance."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    summary = client.get(f"/api/dataset-experiments/{experiment_id}").json()
    items = client.get(f"/api/dataset-experiments/{experiment_id}/items").json()
    attempt_counts = {}
    actual_runs = []
    for item in items:
        attempts = client.get(
            f"/api/dataset-experiments/{experiment_id}/items/{item['id']}/attempts"
        ).json()
        attempt_counts[item["id"]] = len(attempts)
        for attempt in attempts:
            actual_runs.append(attempt["analysis_run_id"])
    evaluation = {}
    if summary.get("dataset_evaluation_id"):
        evaluation = client.get(
            f"/api/dataset-benchmarks/{summary['dataset_evaluation_id']}"
        ).json()
    summary["_items"] = items
    summary["_attempt_counts"] = attempt_counts
    summary["_actual_runs"] = actual_runs
    summary["_actual_run_count"] = len(actual_runs)
    summary["_actual_attempt_count"] = sum(attempt_counts.values())
    summary["_actual_recording_names"] = [i.get("recording_name") for i in items]
    summary["_evaluation"] = evaluation
    summary["_membership"] = {"expected_items": cycle.expected_items}
    summary["_experiment_id"] = experiment_id
    return summary


def run_cycle(app, *, cycle: membership.H5Cycle, name: str, concurrency: int,
              monitor_thread: monitor.ConcurrencyMonitor,
              resource_monitor: monitor.ResourceMonitor,
              gpu_baseline_mib: int) -> dict:
    from fastapi.testclient import TestClient

    # Membership must be exact BEFORE the experiment is created/run.
    membership.assert_cycle_membership(app, cycle)
    experiment_id = create_cycle_experiment(app, cycle=cycle, name=name, concurrency=concurrency)
    client = TestClient(app)
    # Issue 1: BOTH monitors must agree on the SAME active experiment identity.
    monitor_thread.activate(experiment_id)
    resource_monitor.activate(experiment_id)
    client.post(f"/api/dataset-experiments/{experiment_id}/run")

    deadline = time.time() + 3600
    while time.time() < deadline:
        # Issue 2: fail closed if EITHER monitor thread died or aborted live.
        if monitor_thread.abort_reason is not None or monitor_thread.thread_failure is not None:
            raise SystemExit(f"H5 STOP: {monitor_thread.abort_reason or monitor_thread.thread_failure}")
        if resource_monitor.abort_reason is not None or resource_monitor.thread_failure is not None:
            raise SystemExit(f"H5 STOP: {resource_monitor.abort_reason or resource_monitor.thread_failure}")
        body = client.get(f"/api/dataset-experiments/{experiment_id}").json()
        if body["status"] in ("completed", "completed_with_failures", "failed"):
            break
        time.sleep(0.5)
    else:
        raise SystemExit(f"H5 STOP: cycle {cycle.index} did not reach terminal state")

    # Issue 8: exact drain of THIS experiment's workers (fail closed on timeout),
    # keeping both monitors on the same active experiment until workers exit.
    run_ids = _run_ids_for_experiment(app, experiment_id)
    try:
        core.wait_for_exact_workers_drained(run_ids, timeout_s=120)
    except core.WorkerDrainTimeout as exc:
        raise SystemExit(f"H5 STOP: H5_WORKER_DRAIN_TIMEOUT: {exc}") from exc
    monitor_thread.deactivate()
    resource_monitor.deactivate()

    summary = gather_cycle_evidence(app, experiment_id=experiment_id, cycle=cycle)
    acceptance = h5.evaluate_cycle_acceptance(summary=summary, cycle=cycle)
    if acceptance.abort:
        raise SystemExit(f"H5 STOP: CYCLE_ACCEPTANCE_FAILED {acceptance.checks}")

    core.assert_no_orphans(f"H5 cycle {cycle.index + 1}")
    cycle_quiescence = core.assert_gpu_quiescent(gpu_baseline_mib, label=f"H5 cycle {cycle.index + 1}")
    summary["_cycle_acceptance"] = acceptance.checks
    summary["_cycle_quiescence"] = cycle_quiescence
    return summary


def verify_prior_cycles(app, *, cycles, start_cycle: int, root: Path) -> list[dict]:
    """Controlled resume: verify every already-completed cycle from the real DB.

    Never re-runs a completed cycle. Returns the recovered cycle summaries; the
    intended partition stays 16 + 16 + 8 = 40 and remaining cycles continue.
    """
    from fastapi.testclient import TestClient

    client = TestClient(app)
    recovered: list[dict] = []
    existing = client.get("/api/dataset-experiments").json()
    for cycle in cycles[:start_cycle]:
        expected = cycle.expected_items
        used = {s["_experiment_id"] for s in recovered}
        candidates = [
            e for e in existing
            if e["plugin_id"] == PLUGIN and e["status"] == "completed"
            and e["max_concurrency"] == h5.CONCURRENCY_BOUND
            and e["expected_items"] == expected
            and e["id"] not in used
        ]
        if not candidates:
            raise SystemExit(
                f"H5 STOP: resume requires a completed cycle with {expected} items "
                f"before --start-cycle {start_cycle}"
            )
        experiment_id = sorted(c["id"] for c in candidates)[0]
        summary = gather_cycle_evidence(app, experiment_id=experiment_id, cycle=cycle)
        acceptance = h5.evaluate_cycle_acceptance(summary=summary, cycle=cycle)
        if acceptance.abort:
            raise SystemExit(
                f"H5 STOP: prior cycle {cycle.index + 1} acceptance failed: {acceptance.checks}"
            )
        summary["_cycle_acceptance"] = acceptance.checks
        summary["_recovered"] = True
        recovered.append(summary)
        print(json.dumps({"resumed_cycle": cycle.index + 1,
                          "experiment_id": experiment_id,
                          "actual_runs": summary["_actual_run_count"]}, indent=2))
    return recovered


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--start-cycle", type=int, default=0,
                        help="resume from cycle N (1-based); prior cycles must be completed")
    parser.add_argument("--verify-only", action="store_true",
                        help="recover ALL cycles from the DB (zero new executions)")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required (real 40-execution campaign)")
    start_cycle = max(0, args.start_cycle - 1)

    root = common.PLAN_B_ROOT / "h5"

    cycles = membership.h5_cycles()
    if membership.total_expected_executions() != 40:
        raise SystemExit("H5 STOP: cycle partition is not 40")
    if args.verify_only:
        start_cycle = len(cycles)  # recover ALL cycles from the DB (zero launches)

    # Issue 5: exactly-once guard. On a fresh DB only start_cycle==0 is legal;
    # when resuming, prior cycles must already be completed (never re-run).
    fresh = h5.check_h5_fresh_state(root / "qual.db")
    if fresh["fresh"] and start_cycle != 0:
        raise SystemExit("H5 STOP: cannot resume: the H5 DB is fresh")
    if not fresh["fresh"] and start_cycle == 0:
        raise SystemExit(
            f"H5 STOP: H5_EXISTING_STATE (existing state: {fresh['counts']})"
        )

    # Issue 1: before the FIRST H5 DatasetExperiment exists, the qualification-
    # owned worker set is EMPTY, so ANY pre-existing GPU compute process is foreign.
    admission = h5.run_per_campaign_admission(core, plan_b_pids=())
    if not admission["admitted"]:
        raise SystemExit(f"H5 STOP: {admission['reason']}: {admission['detail']}")

    baseline = admission["gpu_memory_baseline_mib"]
    app, _settings = core.build_app(root)
    recovered: list[dict] = verify_prior_cycles(
        app, cycles=cycles, start_cycle=start_cycle, root=root
    )
    view_counts = {
        membership.H5_FULL: membership.register_dataset_view(
            app, dataset_name=membership.H5_FULL, stems=tuple(common.H2_STEMS)
        ),
        membership.H5_TAIL8: membership.register_dataset_view(
            app, dataset_name=membership.H5_TAIL8, stems=tuple(common.H2_STEMS[:8])
        ),
    }

    samples_path = common.EVIDENCE_DIR / (
        "h5_concurrency_samples_verification.jsonl" if args.verify_only
        else "h5_concurrency_samples.jsonl"
    )
    resource_path = common.EVIDENCE_DIR / (
        "h5_resource_samples_verification.jsonl" if args.verify_only
        else "h5_resource_samples.jsonl"
    )

    def _run_ids(experiment_id):
        return _run_ids_for_experiment(app, experiment_id) if experiment_id else []

    concurrency_monitor = monitor.ConcurrencyMonitor(
        app=app,
        evidence_path=samples_path,
        target_period_s=h5.H5_TARGET_SAMPLE_PERIOD_S,
        max_gap_s=h5.H5_MAX_SAMPLE_GAP_S,
        bound=h5.CONCURRENCY_BOUND,
        run_ids_for_experiment=_run_ids,
        gpu_compute_pids=lambda: [pid for pid, _mib in core.compute_apps()],
    )
    concurrency_monitor.start()

    # Issue 1: ResourceMonitor owns its OWN exact active-experiment lifecycle.
    # Issue 2: it instantiates the A1 MemoryMonitor from the admission snapshot.
    resource_monitor = monitor.ResourceMonitor(
        evidence_path=resource_path,
        interval_s=h5.ENDURANCE_SAMPLE_INTERVAL_S,
        gpu_metrics=core.gpu_metrics,
        run_ids_for_experiment=_run_ids,
        a1_baseline_snapshot=admission.get("a1_snapshot"),
        db_counts=lambda: core.db_counts(root),
    )
    resource_monitor.start()

    cycle_summaries = list(recovered)
    try:
        for index, cycle in enumerate(cycles):
            if index < start_cycle:
                continue  # already completed and verified from the real DB
            summary = run_cycle(
                app, cycle=cycle, name=f"Plan B H5 cycle {index + 1}",
                concurrency=h5.CONCURRENCY_BOUND,
                monitor_thread=concurrency_monitor,
                resource_monitor=resource_monitor,
                gpu_baseline_mib=baseline,
            )
            cycle_summaries.append(summary)
    finally:
        concurrency_monitor.stop()
        resource_monitor.stop()

    core.assert_no_orphans("H5 post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H5")

    acceptance = h5.evaluate_final_acceptance(
        cycle_summaries=cycle_summaries,
        concurrency_samples=concurrency_monitor.samples,
        resource_samples=resource_monitor.samples,
        resource_baseline=admission["cgroup_events_baseline"],
        foreign_gpu_pids=monitor.foreign_compute_pids(concurrency_monitor.samples),
        concurrency_monitor_failure=concurrency_monitor.thread_failure,
        resource_monitor_failure=resource_monitor.thread_failure,
        concurrency_artifact_present=samples_path.exists() and samples_path.stat().st_size > 0,
        resource_artifact_present=resource_path.exists() and resource_path.stat().st_size > 0,
    )

    evidence = {
        "milestone": "H5",
        "experiment_ids": [s["_experiment_id"] for s in cycle_summaries],
        "view_counts": view_counts,
        "cycles": [c.expected_items for c in cycles],
        "expected_executions": membership.total_expected_executions(),
        "admission": admission,
        "acceptance": acceptance,
        "gpu_quiescence": quiescent,
        "concurrency_samples": len(concurrency_monitor.samples),
        "resource_samples": len(resource_monitor.samples),
        "concurrency_monitor_failure": concurrency_monitor.thread_failure,
        "resource_monitor_failure": resource_monitor.thread_failure,
        "raw_concurrency_artifact": str(samples_path),
        "raw_resource_artifact": str(resource_path),
    }
    path = core.write_evidence(
        "h5_concurrency_endurance_verification.json" if args.verify_only
        else "h5_concurrency_endurance.json",
        evidence,
    )
    print(json.dumps({"experiment_ids": evidence["experiment_ids"],
                      "acceptance": acceptance, "evidence": str(path)}, indent=2))
    return 0 if acceptance["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

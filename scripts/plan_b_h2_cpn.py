"""Plan B H2 — CPN ×16 DatasetExperiment, concurrency=1 (also H3 Experiment A).

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_h2_cpn.py

16 real local_gpu model executions. Exactly once.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402
import plan_b_dataset_core as core  # noqa: E402

PLUGIN = "cpn_bandwidth_tier"
CPN_MANIFEST = "7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb"


def _acceptance(app, experiment, items, evaluation) -> dict:
    def counts(status):
        return [i for i in items if i["status"] == status]

    completed = counts("completed")
    failed = counts("failed")
    queued = counts("queued")
    running = counts("running")

    attempts_per_item = {
        item["id"]: core.fetch_attempts(app, experiment["id"], item["id"]) for item in items
    }
    single_attempt = all(len(a) == 1 and a[0]["attempt_number"] == 1 for a in attempts_per_item.values())

    descriptor = experiment["runtime_descriptor_json"]
    agg = evaluation.get("aggregate_metrics_json") or {}
    localization = agg.get("localization") or {}

    checks = {
        "expected_items": experiment["expected_items"] == 16,
        "all_terminal": experiment["status"] == "completed",
        "completed_16": len(completed) == 16,
        "failed_0": len(failed) == 0,
        "queued_0": len(queued) == 0,
        "running_0": len(running) == 0,
        "executor_local_gpu": experiment["executor"] == "local_gpu",
        "descriptor_executor": descriptor.get("executor") == "local_gpu",
        "descriptor_device_type": descriptor.get("device_type") == "cuda",
        "descriptor_precision": descriptor.get("precision") == "float16",
        "descriptor_environment_label": descriptor.get("environment_label") == common.GPU_RUNTIME_REF,
        "model_release_golden": experiment["model_release_id"] == "golden",
        "asset_manifest_cpn": experiment["asset_manifest_sha256"] == CPN_MANIFEST,
        "single_attempt_per_item": single_attempt,
        "evaluation_completed": evaluation["status"] == "completed",
        "evaluated_16": evaluation["evaluated_recordings"] == 16,
        "missing_0": evaluation["missing_recordings"] == 0,
        "coverage_1": evaluation["coverage"] == 1.0,
        "comparable": evaluation["comparable"] is True,
        "localization_ap50_numeric": isinstance(localization.get("ap50"), (int, float)),
        "localization_ap50_95_numeric": isinstance(localization.get("ap50_95"), (int, float)),
        "classification_na": agg.get("classification_on_matched") is None,
        "class_aware_na": agg.get("class_aware") is None,
    }
    if not all(checks.values()):
        failed_checks = {k: v for k, v in checks.items() if not v}
        raise SystemExit(f"H2 STOP: acceptance checks failed: {failed_checks}")
    return {
        "checks": checks,
        "completed": len(completed),
        "failed": len(failed),
    }


def main() -> int:
    h2root = common.PLAN_B_ROOT / "h2h3"
    baseline = core.gpu_memory_used_mib()
    if core.compute_apps():
        raise SystemExit(f"H2 STOP: GPU compute apps present before start: {core.compute_apps()}")

    app, settings = core.build_app(h2root)
    core.register_subset(app)
    manifest = core.manifest_preview(app)
    if manifest["expected_recordings"] != 16:
        raise SystemExit(f"H2 STOP: manifest expected {manifest['expected_recordings']} != 16")

    experiment_id = core.create_experiment(app, plugin_id=PLUGIN, name="Plan B H2 CPN local_gpu x16")
    core.assert_no_orphans("H2 pre-run")
    experiment = core.run_experiment(app, experiment_id)
    items = core.fetch_items(app, experiment_id)

    evaluation = core.fetch_evaluation(app, experiment["dataset_evaluation_id"])
    core.assert_no_orphans("H2 post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H2")

    acceptance = _acceptance(app, experiment, items, evaluation)

    # Re-prepare must be stable.
    manifest_again = core.manifest_preview(app)
    manifest_stable = manifest_again["recording_manifest_hash"] == manifest["recording_manifest_hash"]

    evidence = {
        "milestone": "H2",
        "experiment_id": experiment_id,
        "plugin_id": PLUGIN,
        "executor": "local_gpu",
        "max_concurrency": 1,
        "executions": 16,
        "manifest": manifest,
        "manifest_stable": manifest_stable,
        "experiment_status": experiment["status"],
        "evaluation_id": experiment["dataset_evaluation_id"],
        "evaluation": {
            "status": evaluation["status"],
            "evaluated_recordings": evaluation["evaluated_recordings"],
            "missing_recordings": evaluation["missing_recordings"],
            "coverage": evaluation["coverage"],
            "comparable": evaluation["comparable"],
            "aggregate_metrics_json": evaluation.get("aggregate_metrics_json"),
        },
        "acceptance": acceptance,
        "gpu_quiescence": quiescent,
    }
    path = core.write_evidence("h2_cpn.json", evidence)
    if not manifest_stable:
        raise SystemExit("H2 STOP: manifest hash not stable across re-prepare")
    print(json.dumps({"experiment_id": experiment_id, "status": experiment["status"],
                      "evidence": str(path), "coverage": evaluation["coverage"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

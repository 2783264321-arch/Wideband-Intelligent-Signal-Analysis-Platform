"""Plan B H3 — ZoomSpec ×16 + same-manifest multi-model comparison.

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_h3_zoomspec.py

Reuses H2 Experiment A (CPN, already completed in the same DB). Creates ONLY the
ZoomSpec Experiment B over the identical frozen 16-stem membership (16 real
local_gpu executions), then runs the existing benchmark comparison flow.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402
import plan_b_dataset_core as core  # noqa: E402

PLUGIN = "zoomspec_yolo26n_aug_combined_frn_v3"
ZOOMSPEC_MANIFEST = "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08"
H2_EXPERIMENT_ID = "exp_10bbbdf31d7b479d84bd07f0207d4c64"


def _resolve_h2_experiment(app) -> dict:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    body = client.get(f"/api/dataset-experiments/{H2_EXPERIMENT_ID}")
    if body.status_code != 200:
        raise SystemExit(f"H3 STOP: H2 experiment not found: {body.status_code}")
    exp = body.json()
    if exp["plugin_id"] != "cpn_bandwidth_tier" or exp["status"] != "completed":
        raise SystemExit(f"H3 STOP: H2 Experiment A is not the completed CPN experiment: {exp['status']}")
    return exp


def _acceptance(experiment, items, evaluation, h2_experiment, h2_evaluation) -> dict:
    completed = [i for i in items if i["status"] == "completed"]
    descriptor = experiment["runtime_descriptor_json"]
    agg = evaluation.get("aggregate_metrics_json") or {}
    localization = agg.get("localization") or {}

    checks = {
        "manifest_equal": experiment["recording_manifest_hash"] == h2_experiment["recording_manifest_hash"],
        "dataset_equal": (
            experiment["dataset_name"] == h2_experiment["dataset_name"]
            and experiment["dataset_split"] == h2_experiment["dataset_split"]
            and experiment["dataset_label_space"] == h2_experiment["dataset_label_space"]
        ),
        "all_terminal": experiment["status"] == "completed",
        "completed_16": len(completed) == 16,
        "executor_local_gpu": experiment["executor"] == "local_gpu",
        "descriptor_executor": descriptor.get("executor") == "local_gpu",
        "descriptor_device_type": descriptor.get("device_type") == "cuda",
        "descriptor_precision": descriptor.get("precision") == "float16",
        "model_release_golden": experiment["model_release_id"] == "golden",
        "asset_manifest_zoomspec": experiment["asset_manifest_sha256"] == ZOOMSPEC_MANIFEST,
        "h2_asset_manifest_cpn": h2_experiment["asset_manifest_sha256"] != ZOOMSPEC_MANIFEST,
        "evaluation_completed": evaluation["status"] == "completed",
        "evaluated_16": evaluation["evaluated_recordings"] == 16,
        "missing_0": evaluation["missing_recordings"] == 0,
        "coverage_1": evaluation["coverage"] == 1.0,
        "comparable": evaluation["comparable"] is True,
        "h2_coverage_1": h2_evaluation["coverage"] == 1.0,
        "localization_ap50_numeric": isinstance(localization.get("ap50"), (int, float)),
        "localization_ap50_95_numeric": isinstance(localization.get("ap50_95"), (int, float)),
    }
    if not all(checks.values()):
        failed_checks = {k: v for k, v in checks.items() if not v}
        raise SystemExit(f"H3 STOP: acceptance checks failed: {failed_checks}")
    return {"checks": checks, "completed": len(completed)}


def main() -> int:
    h2root = common.PLAN_B_ROOT / "h2h3"
    baseline = core.gpu_memory_used_mib()
    if core.compute_apps():
        raise SystemExit(f"H3 STOP: GPU compute apps present before start: {core.compute_apps()}")

    app, settings = core.build_app(h2root)
    h2_experiment = _resolve_h2_experiment(app)
    h2_evaluation = core.fetch_evaluation(app, h2_experiment["dataset_evaluation_id"])
    manifest = core.manifest_preview(app)
    if manifest["recording_manifest_hash"] != h2_experiment["recording_manifest_hash"]:
        raise SystemExit("H3 STOP: current manifest differs from the H2 frozen manifest")

    experiment_id = core.create_experiment(app, plugin_id=PLUGIN, name="Plan B H3 ZoomSpec local_gpu x16")
    core.assert_no_orphans("H3 pre-run")
    experiment = core.run_experiment(app, experiment_id)
    items = core.fetch_items(app, experiment_id)
    evaluation = core.fetch_evaluation(app, experiment["dataset_evaluation_id"])
    core.assert_no_orphans("H3 post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H3")

    acceptance = _acceptance(experiment, items, evaluation, h2_experiment, h2_evaluation)

    comparison = _compare(app, h2_experiment["dataset_evaluation_id"], experiment["dataset_evaluation_id"])
    per_recording = _per_recording(app, h2_experiment, experiment)

    evidence = {
        "milestone": "H3",
        "experiment_id": experiment_id,
        "experiment_a": H2_EXPERIMENT_ID,
        "experiment_b": experiment_id,
        "plugin_id": PLUGIN,
        "executor": "local_gpu",
        "max_concurrency": 1,
        "executions": 16,
        "manifest": manifest,
        "manifest_equal_to_h2": experiment["recording_manifest_hash"] == h2_experiment["recording_manifest_hash"],
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
        "comparison": comparison,
        "per_recording": per_recording,
        "gpu_quiescence": quiescent,
    }
    path = core.write_evidence("h3_zoomspec.json", evidence)
    print(json.dumps({"experiment_id": experiment_id, "status": experiment["status"],
                      "coverage": evaluation["coverage"],
                      "comparison_comparable": comparison["comparable"],
                      "evidence": str(path)}, indent=2))
    return 0


def _compare(app, eval_a: str, eval_b: str) -> dict:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post("/api/dataset-benchmarks/compare",
                       json={"evaluation_a_id": eval_a, "evaluation_b_id": eval_b})
    if resp.status_code != 200:
        raise SystemExit(f"H3 STOP: compare -> {resp.status_code} {resp.text}")
    body = resp.json()
    if body.get("comparable") is not True or body.get("reasons"):
        raise SystemExit(f"H3 STOP: comparison not comparable: {body.get('reasons')}")
    deltas = body.get("deltas") or {}
    if not isinstance(deltas.get("localization_ap50"), (int, float)):
        raise SystemExit(f"H3 STOP: localization_ap50 delta not numeric: {deltas.get('localization_ap50')}")
    if not isinstance(deltas.get("localization_ap50_95"), (int, float)):
        raise SystemExit(f"H3 STOP: localization_ap50_95 delta not numeric: {deltas.get('localization_ap50_95')}")
    for key in ("class_aware_map50", "class_aware_map50_95", "matched_accuracy"):
        if deltas.get(key) is not None:
            raise SystemExit(f"H3 STOP: expected N/A delta for {key}: {deltas.get(key)}")
    return {"comparable": body["comparable"], "reasons": body["reasons"], "deltas": deltas}


def _per_recording(app, h2_experiment, experiment) -> dict:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    items_h2 = core.fetch_items(app, H2_EXPERIMENT_ID)
    items_b = core.fetch_items(app, experiment["id"])
    by_recording_a = {i["recording_id"]: i["latest_analysis_run_id"] for i in items_h2}
    by_recording_b = {i["recording_id"]: i["latest_analysis_run_id"] for i in items_b}
    shared = sorted(set(by_recording_a) & set(by_recording_b))
    compared = 0
    for recording_id in shared:
        resp = client.post("/api/algorithm-lab/compare", json={
            "recording_id": recording_id,
            "run_a_id": by_recording_a[recording_id],
            "run_b_id": by_recording_b[recording_id],
            "iou_threshold": 0.5,
        })
        if resp.status_code != 200:
            raise SystemExit(f"H3 STOP: algorithm-lab compare failed for {recording_id}: {resp.text}")
        compared += 1
    return {"shared_recordings": len(shared), "per_recording_compares": compared}

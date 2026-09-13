"""BHQ-3 Task 9 — committed direct-science CUDA oracle (run under the ML interpreter).

Runs ONE frozen direct-science case per process (current tree) and writes full
JSON evidence (physical TF boxes, classes, confidence, run_metadata/stage counts)
so parity does not depend on any external/temporary scratch.

    PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python \
        scripts/bhq3_direct_science_oracle.py --plugin cpn --stem 2 \
        --out /tmp/bhq3_work/direct_science_oracle_cpn_stem2.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bhq3_common as common  # noqa: E402


def _serialize(out) -> dict:
    return {
        "detections": [
            {
                "t_start_s": d.t_start_s,
                "t_end_s": d.t_end_s,
                "f_low_hz": d.f_low_hz,
                "f_high_hz": d.f_high_hz,
                "class_id": d.class_id,
                "class_name": d.class_name,
                "confidence": d.confidence,
                "scores": d.scores,
            }
            for d in out.detections
        ],
        "run_metadata": dict(out.run_metadata),
    }


def run_cpn(stem: str, workspace: Path) -> dict:
    from app.labels.service import LabelSpaceService
    from app.pipelines.cpn_bandwidth_tier.pipeline import CPNBandwidthTierPipeline

    recording, sample = common.recording_input(stem)
    pipeline = CPNBandwidthTierPipeline(
        detector_checkpoint_path=common.DETECTOR,
        normalization=common.load_normalization(),
        label_space=LabelSpaceService(common.LABELS).get("cpn_bandwidth_tier_v1"),
        device=0,
    )
    t0 = time.perf_counter()
    out = pipeline.run(recording, {}, workspace)
    wall = time.perf_counter() - t0
    return {"plugin": "cpn", "stem": stem, "wall_time_s": wall, **_serialize(out)}


def run_zoomspec(stem: str, workspace: Path) -> dict:
    from app.labels.service import LabelSpaceService
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import ZoomSpecFrozenPipeline

    recording, sample = common.recording_input(stem)
    pipeline = ZoomSpecFrozenPipeline(
        detector_checkpoint_path=common.DETECTOR,
        frn_checkpoint_path=common.FRN,
        normalization=common.load_normalization(),
        label_space=LabelSpaceService(common.LABELS).get("spacenet_14"),
        device=0,
    )
    t0 = time.perf_counter()
    out = pipeline.run(recording, {}, workspace)
    wall = time.perf_counter() - t0
    return {"plugin": "zoomspec", "stem": stem, "wall_time_s": wall, **_serialize(out)}


def _run_child(plugin: str, stem: str, out_path: Path) -> int:
    """Child: run exactly the frozen science and write the result JSON."""
    common.ensure_work_root()
    workspace = common.WORK_ROOT / "oracle" / f"{plugin}_stem{stem}"
    workspace.mkdir(parents=True, exist_ok=True)

    plugin_id = "cpn_bandwidth_tier" if plugin == "cpn" else "zoomspec_yolo26n_aug_combined_frn_v3"
    result = {
        "plugin": plugin,
        "stem": stem,
        "assets": common.verify_assets(plugin_id),
    }
    if plugin == "cpn":
        result.update(run_cpn(stem, workspace))
    else:
        result.update(run_zoomspec(stem, workspace))

    common.write_json(out_path, result)
    print(json.dumps({
        "plugin": result["plugin"], "stem": result["stem"],
        "detection_count": len(result["detections"]),
        "run_metadata": result["run_metadata"],
        "wall_time_s": round(result["wall_time_s"], 3),
    }, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", choices=["cpn", "zoomspec"], required=True)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--child", action="store_true", default=False,
                        help="internal: run exactly one science case (no admission/guard)")
    args = parser.parse_args(argv)

    if args.child:
        return _run_child(args.plugin, args.stem, Path(args.out))

    # Controller: two-path admission + live cgroup guard around ONE science child.
    common.ensure_work_root()
    child_argv = [
        sys.executable, str(Path(__file__).resolve()),
        "--child", "--plugin", args.plugin, "--stem", args.stem, "--out", args.out,
    ]
    outcome = common.run_guarded_subprocess(child_argv)
    if outcome["abort_reason"]:
        raise SystemExit("BHQ_3_BLOCKED_BY_CGROUP_MEMORY: " + outcome["abort_reason"])
    if outcome["returncode"] != 0:
        raise SystemExit(f"BHQ_3 oracle child failed rc={outcome['returncode']}")

    result = json.loads(Path(args.out).read_text(encoding="utf-8"))
    print(json.dumps({
        "plugin": result["plugin"], "stem": result["stem"],
        "detection_count": len(result["detections"]),
        "run_metadata": result["run_metadata"],
        "wall_time_s": round(result["wall_time_s"], 3),
        "memory_gate_mode": outcome["gate"]["mode"],
        "memory_gate_samples": outcome["samples"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

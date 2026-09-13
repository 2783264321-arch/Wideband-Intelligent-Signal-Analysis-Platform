"""BHQ-3 Task 11 — post-certification real production-path local_gpu AnalysisRuns.

Uses the REAL committed certificate store (no injection) and the real app
registry. Runs one real case per subprocess.

    PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" \
        scripts/bhq3_local_gpu_production_acceptance.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import bhq3_common as common  # noqa: E402

ALL_CASES = [
    ("cpn_bandwidth_tier", "2"),
    ("zoomspec_yolo26n_aug_combined_frn_v3", "2"),
]


def _run_single(plugin_id: str, stem: str) -> int:
    import bhq3_acceptance_core as core

    evidence = core.run_case(plugin_id, stem, mode="prod", inject_temp_cert=False)
    projection = core.projection_for(plugin_id)

    if "local_gpu" not in projection["read_model_executors_supported"]:
        raise SystemExit(
            "BHQ_3_BLOCKED_BY_PRODUCTION_PATH_FAILED: local_gpu missing from "
            f"executors_supported for {plugin_id}: {projection}"
        )
    if projection["read_model_recommended_executor"] != projection["recomputed_recommended"]:
        raise SystemExit(
            "BHQ_3_BLOCKED_BY_PRODUCTION_PATH_FAILED: recommended_executor is not the "
            f"deployment-qualified projection: {projection}"
        )
    evidence["projection"] = projection
    common.write_json(
        common.WORK_ROOT / f"bhq3_prod_evidence_{plugin_id}_stem{stem}.json", evidence
    )
    return 0


def _orchestrate(cases) -> int:
    common.ensure_work_root()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(common.BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    merged = []
    for plugin_id, stem in cases:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--plugin", plugin_id, "--stem", stem]
        proc = subprocess.run(cmd, env=env, cwd=str(common.REPO))
        if proc.returncode != 0:
            print(f"case {plugin_id} stem {stem} FAILED (exit {proc.returncode})", file=sys.stderr)
            return proc.returncode
        path = common.WORK_ROOT / f"bhq3_prod_evidence_{plugin_id}_stem{stem}.json"
        merged.append(json.loads(path.read_text(encoding="utf-8")))
    out = common.WORK_ROOT / "bhq3_production_evidence.json"
    common.write_json(out, {"mode": "prod", "cases": merged})
    print(json.dumps({"combined_evidence": str(out), "cases": len(merged)}, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", choices=["cpn_bandwidth_tier", "zoomspec_yolo26n_aug_combined_frn_v3"])
    parser.add_argument("--stem", choices=["2", "0"])
    parser.add_argument("--all", action="store_true", default=False)
    args = parser.parse_args(argv)

    if args.all or (args.plugin is None and args.stem is None):
        return _orchestrate(ALL_CASES)
    if args.plugin is None or args.stem is None:
        parser.error("--plugin and --stem are required unless --all is given")
    return _run_single(args.plugin, args.stem)


if __name__ == "__main__":
    raise SystemExit(main())

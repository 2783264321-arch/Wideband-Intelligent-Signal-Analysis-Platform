"""BHQ-3 Task 8 — pre-cert real local_gpu hardware acceptance (temporary in-memory cert).

Runs one real case per subprocess (fresh process per case) under the control-plane
`.venv`; the worker runs under /root/miniconda3/bin/python.

    PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" \
        scripts/bhq3_local_gpu_acceptance.py --all

The temporary local_gpu certificate is constructed in memory ONLY and is never
written to backend/app/pipelines/execution_certificates.json.
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
    ("cpn_bandwidth_tier", "0"),
    ("zoomspec_yolo26n_aug_combined_frn_v3", "2"),
    ("zoomspec_yolo26n_aug_combined_frn_v3", "0"),
]


def _run_single(plugin_id: str, stem: str) -> int:
    import bhq3_acceptance_core as core

    core.run_case(plugin_id, stem, mode="tempcert", inject_temp_cert=True)
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
        evidence_path = common.WORK_ROOT / f"bhq3_tempcert_evidence_{plugin_id}_stem{stem}.json"
        merged.append(json.loads(evidence_path.read_text(encoding="utf-8")))
    combined = {"mode": "tempcert", "certificate": "temporary in-memory only", "cases": merged}
    out = common.WORK_ROOT / "bhq3_temp_cert_evidence.json"
    common.write_json(out, combined)
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

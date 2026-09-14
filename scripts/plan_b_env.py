"""Plan B operator entry point (B2): init the Plan-B work roots and verify config.

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_env.py --init
    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_env.py --verify
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402


def _init() -> int:
    roots = [common.PLAN_B_ROOT / name for name in ("b3", "h2h3", "h4", "h5")]
    for root in roots + [common.EVIDENCE_DIR]:
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / "work").mkdir(parents=True, exist_ok=True)
    common.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"initialized": sorted(str(p) for p in roots + [common.EVIDENCE_DIR])}, indent=2))
    return 0


def _verify() -> int:
    report = common.verify_configuration()
    common.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = common.EVIDENCE_DIR / "b2_config_verification.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    ok = (
        report["interpreter_exists"]
        and report["gpu_identity_match"]
        and report["assets_present"]
        and report["space_net_present"]
    )
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if args.init:
        return _init()
    if args.verify:
        return _verify()
    parser.error("choose --init or --verify")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Plan B post-hoc evidence hardening: assert the EXISTING H2 CPN evaluation
records the exact classification non-applicability reason.

CPU-only, read-only. NEVER reruns H2 inference. Reads the dedicated Plan-B DB and
the preserved H2 evidence artifact, and writes a small supplementary evidence
file proving the N/A reason is ``label_space_mismatch``.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402

EXPECTED_REASON = "label_space_mismatch"
H2_DB = common.PLAN_B_ROOT / "h2h3" / "qual.db"
H2_EVIDENCE = common.EVIDENCE_DIR / "h2_cpn.json"


def verify_from_db(db_path: Path = H2_DB) -> dict:
    connection = sqlite3.connect(str(db_path))
    row = connection.execute(
        "select dataset_evaluation_id from dataset_experiments "
        "where plugin_id = 'cpn_bandwidth_tier' and status = 'completed'"
    ).fetchone()
    if row is None:
        raise SystemExit("no completed CPN experiment in the H2 DB")
    evaluation_id = row[0]
    aggregate_row = connection.execute(
        "select aggregate_metrics_json from dataset_evaluations where id = ?",
        (evaluation_id,),
    ).fetchone()
    if aggregate_row is None:
        raise SystemExit("H2 evaluation row not found")
    aggregate = json.loads(aggregate_row[0])
    return {
        "evaluation_id": evaluation_id,
        "classification_applicable": aggregate.get("classification_applicable"),
        "classification_reason": aggregate.get("classification_reason"),
        "classification_on_matched": aggregate.get("classification_on_matched"),
        "class_aware": aggregate.get("class_aware"),
    }


def verify_from_evidence(path: Path = H2_EVIDENCE) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    aggregate = payload["evaluation"]["aggregate_metrics_json"]
    return {
        "evaluation_id": payload["evaluation_id"],
        "classification_applicable": aggregate.get("classification_applicable"),
        "classification_reason": aggregate.get("classification_reason"),
        "classification_on_matched": aggregate.get("classification_on_matched"),
        "class_aware": aggregate.get("class_aware"),
    }


def verify() -> dict:
    from_db = verify_from_db()
    from_evidence = verify_from_evidence()
    checks = {
        "db_applicable_false": from_db["classification_applicable"] is False,
        "db_reason_exact": from_db["classification_reason"] == EXPECTED_REASON,
        "evidence_applicable_false": from_evidence["classification_applicable"] is False,
        "evidence_reason_exact": from_evidence["classification_reason"] == EXPECTED_REASON,
        "db_evidence_evaluation_ids_match": from_db["evaluation_id"] == from_evidence["evaluation_id"],
    }
    if not all(checks.values()):
        raise SystemExit(f"H2 N/A reason verification FAILED: {checks}")
    return {
        "expected_reason": EXPECTED_REASON,
        "db": from_db,
        "evidence": from_evidence,
        "checks": checks,
        "all_pass": True,
        "inference_reran": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if not args.verify:
        parser.error("--verify is required")
    report = verify()
    common.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = common.EVIDENCE_DIR / "h2_na_reason_verification.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

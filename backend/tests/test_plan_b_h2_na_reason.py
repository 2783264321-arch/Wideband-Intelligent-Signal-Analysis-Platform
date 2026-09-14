"""Plan B: H2 CPN classification N/A reason post-hoc verification (CPU-only).

Does NOT rerun H2. Proves the EXISTING H2 evaluation records the exact approved
non-applicability reason (``label_space_mismatch``) and that classification
diagnostics are absent.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_h2_na_reason as na  # noqa: E402


def test_existing_h2_na_reason_is_exact() -> None:
    report = na.verify()
    assert report["all_pass"] is True
    assert report["expected_reason"] == "label_space_mismatch"
    assert report["db"]["classification_applicable"] is False
    assert report["db"]["classification_reason"] == "label_space_mismatch"
    assert report["db"]["classification_on_matched"] is None
    assert report["db"]["class_aware"] is None
    assert report["evidence"]["classification_reason"] == "label_space_mismatch"
    assert report["inference_reran"] is False


def test_na_verifier_is_read_only_source() -> None:
    source = (SCRIPTS / "plan_b_h2_na_reason.py").read_text(encoding="utf-8")
    for forbidden in ("nvidia-smi", "local_inference_worker", "torch", "create_app"):
        assert forbidden not in source

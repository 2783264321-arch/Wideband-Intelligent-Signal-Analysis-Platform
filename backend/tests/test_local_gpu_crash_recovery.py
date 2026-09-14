"""Plan B H4: genuine local_gpu crash recovery (gated, real GPU 2 cases)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


@pytest.mark.skipif(
    os.environ.get("WSP_PLAN_B_REAL_GPU") != "1",
    reason="WSP_PLAN_B_REAL_GPU=1 not set",
)
def test_real_local_gpu_crash_recovery_live() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "backend") + os.pathsep + str(SCRIPTS) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "plan_b_h4_recovery.py"), "--live"],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=1800,
    )
    assert result.returncode == 0, result.stderr or result.stdout

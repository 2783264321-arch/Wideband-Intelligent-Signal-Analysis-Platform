"""Subprocess launcher for the per-run remote coordinator.

Launches ONE local coordinator subprocess per remote ``AnalysisRun`` using a
fixed argv to ``app.remote_execution.coordinator`` and passes the per-launch
``coordinator_token``. It never invokes ``app.analysis.worker``.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from app.core.config import Settings


class CoordinatorJobManager:
    DEFAULT_COORDINATOR_MODULE = "app.remote_execution.coordinator"
    DEFAULT_PYTHON_EXECUTABLE = sys.executable

    def __init__(self, settings: Settings, *, popen_factory=None):
        self.settings = settings
        self.backend_root = Path(__file__).resolve().parents[2]
        self._popen_factory = popen_factory or subprocess.Popen

    def launch(
        self,
        run_id: str,
        coordinator_token: str,
        *,
        coordinator_module: str = None,
    ) -> int:
        module = coordinator_module or self.DEFAULT_COORDINATOR_MODULE
        env = os.environ.copy()
        env.update(
            {
                "WSP_PROJECT_ROOT": str(self.settings.project_root),
                "WSP_DATA_ROOT": str(self.settings.data_root),
                "WSP_LABEL_SPACE_ROOT": str(self.settings.label_space_root),
                "WSP_DATABASE_URL": str(self.settings.database_url),
            }
        )
        process = self._popen_factory(
            [sys.executable, "-m", module, run_id, "--coordinator-token", coordinator_token],
            cwd=self.backend_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        return process.pid
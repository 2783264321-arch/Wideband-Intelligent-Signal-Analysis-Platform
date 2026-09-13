from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from app.core.config import Settings


class DatasetExperimentJobManager:
    DEFAULT_COORDINATOR_MODULE = "app.dataset_experiments.worker"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.backend_root = Path(__file__).resolve().parents[2]

    def start(self, experiment_id: str, coordinator_token: str) -> int:
        env = os.environ.copy()
        env.update(
            {
                "WSP_PROJECT_ROOT": str(self.settings.project_root),
                "WSP_DATA_ROOT": str(self.settings.data_root),
                "WSP_LABEL_SPACE_ROOT": str(self.settings.label_space_root),
                "WSP_DATABASE_URL": str(self.settings.database_url),
            }
        )
        process = subprocess.Popen(
            [sys.executable, "-m", self.DEFAULT_COORDINATOR_MODULE,
             experiment_id, "--coordinator-token", coordinator_token],
            cwd=self.backend_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        return process.pid

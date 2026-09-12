import subprocess
import sys
from pathlib import Path

from app.analysis.model import AnalysisRunModel


def test_control_plane_import_does_not_load_torch_or_ultralytics():
    code = (
        "import sys; "
        "import app.dataset_experiments.service; "
        "assert 'torch' not in sys.modules, 'torch leaked into G1 control plane'; "
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked into G1 control plane'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_analysis_run_schema_has_no_dataset_experiment_columns():
    columns = set(AnalysisRunModel.__table__.columns.keys())
    assert not any("experiment" in name for name in columns)
    assert "launch_requested_at" not in columns

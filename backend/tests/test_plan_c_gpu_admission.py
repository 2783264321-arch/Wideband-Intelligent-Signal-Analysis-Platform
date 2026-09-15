"""C-pre-3 — Plan-C foreign-GPU admission / acceptance tooling (acceptance-only).

Behavior tests for ``scripts/plan_c_gpu_admission.py``. The subprocess runner is
always injected, so these tests never touch a real GPU and never signal any
process. Pure parsers are exercised with synthetic nvidia-smi text.

Gates covered:
    G1  clean target GPU                      -> admitted / exit 0
    G2  foreign process on target GPU         -> blocked_compute_present / exit 3
    G3  process on another GPU only           -> target still admitted
    G4  multiple target processes             -> count exact, blocked
    G5  target GPU query failure              -> fail closed
    G6  compute-app query failure             -> fail closed
    G7  malformed target row                  -> fail closed
    G8  malformed compute row                 -> fail closed
    G9  missing nvidia-smi                    -> fail closed
    G10 fixed argv / shell=False / no shell injection
    G11 process-name bounding                 -> basename + length bound
    G12 production isolation                  -> no app imports, no process control
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
APP = REPO / "backend" / "app"

_SPEC = importlib.util.spec_from_file_location(
    "plan_c_gpu_admission", SCRIPTS / "plan_c_gpu_admission.py"
)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

TARGET_UUID = "GPU-11111111-2222-3333-4444-555555555555"
OTHER_UUID = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Deterministic runner. Returns queued results in call order; records argv."""

    def __init__(self, results) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _target_row(
    index: int = 0,
    uuid: str = TARGET_UUID,
    name: str = "NVIDIA GeForce RTX 5090",
    memory_used: str = "123",
    utilization: str = "7",
) -> str:
    return f"{index}, {uuid}, {name}, {memory_used}, {utilization}"


def _compute_row(
    uuid: str = TARGET_UUID,
    pid: str = "4242",
    process_name: str = "python",
    used_memory: str = "512",
) -> str:
    return f"{uuid}, {pid}, {process_name}, {used_memory}"


def _run(device_index: int = 0, target: str | None = None, compute: str | None = None):
    runner = FakeRunner([
        FakeResult(0, _target_row() if target is None else target, ""),
        FakeResult(0, "" if compute is None else compute, ""),
    ])
    result = gate.inspect_gpu_admission(device_index=device_index, runner=runner)
    return result, gate.admission_exit_code(result), runner


# ---------------------------------------------------------------------------
# G1 — clean target GPU
# ---------------------------------------------------------------------------


def test_g1_clean_target_gpu_is_admitted():
    result, code, runner = _run(compute="")
    assert result["status"] == "admitted"
    assert result["compute_process_count"] == 0
    assert result["compute_processes"] == []
    assert code == 0
    assert result["device_index"] == 0
    assert result["gpu_uuid"] == TARGET_UUID
    assert result["gpu_name"] == "NVIDIA GeForce RTX 5090"
    assert result["memory_used_mib"] == 123
    assert result["utilization_gpu_percent"] == 7
    assert len(runner.calls) == 2


def test_g1b_whitespace_only_compute_output_is_admitted():
    result, code, _ = _run(compute="\n  \n")
    assert result["status"] == "admitted"
    assert result["compute_process_count"] == 0
    assert code == 0


# ---------------------------------------------------------------------------
# G2 — foreign process on target GPU
# ---------------------------------------------------------------------------


def test_g2_foreign_process_blocks_and_takes_no_action():
    result, code, runner = _run(compute=_compute_row())
    assert result["status"] == "blocked_compute_present"
    assert result["compute_process_count"] == 1
    assert code == 3
    assert result["compute_processes"][0]["pid"] == 4242
    # only the two fixed queries, nothing more
    assert len(runner.calls) == 2


# ---------------------------------------------------------------------------
# G3 — process on another GPU only
# ---------------------------------------------------------------------------


def test_g3_other_gpu_workload_does_not_block_target():
    result, code, _ = _run(compute=_compute_row(uuid=OTHER_UUID))
    assert result["status"] == "admitted"
    assert result["compute_process_count"] == 0
    assert code == 0


def test_g3b_mixed_other_and_target_gpus_filters_target_only():
    compute = "\n".join([_compute_row(uuid=OTHER_UUID, pid="1"), _compute_row(uuid=TARGET_UUID, pid="2")])
    result, code, _ = _run(compute=compute)
    assert result["status"] == "blocked_compute_present"
    assert result["compute_process_count"] == 1
    assert result["compute_processes"][0]["pid"] == 2
    assert code == 3


# ---------------------------------------------------------------------------
# G4 — multiple target processes
# ---------------------------------------------------------------------------


def test_g4_multiple_target_processes_reported_exactly():
    compute = "\n".join([
        _compute_row(pid="11", process_name="python"),
        _compute_row(pid="22", process_name="python3.12"),
        _compute_row(pid="33", process_name="/opt/x/train"),
    ])
    result, code, _ = _run(compute=compute)
    assert result["status"] == "blocked_compute_present"
    assert result["compute_process_count"] == 3
    assert [p["pid"] for p in result["compute_processes"]] == [11, 22, 33]
    assert code == 3


# ---------------------------------------------------------------------------
# G5/G6 — telemetry failures
# ---------------------------------------------------------------------------


def test_g5_target_query_failure_fails_closed():
    runner = FakeRunner([FakeResult(1, "", "boom")])
    result = gate.inspect_gpu_admission(device_index=0, runner=runner)
    assert result["status"] == "blocked_telemetry_unavailable"
    assert gate.admission_exit_code(result) == 2
    assert result["compute_process_count"] is None
    assert len(runner.calls) == 1


def test_g6_compute_query_failure_fails_closed():
    runner = FakeRunner([FakeResult(0, _target_row(), ""), FakeResult(1, "", "boom")])
    result = gate.inspect_gpu_admission(device_index=0, runner=runner)
    assert result["status"] == "blocked_telemetry_unavailable"
    assert gate.admission_exit_code(result) == 2


# ---------------------------------------------------------------------------
# G7/G8 — malformed rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_target", [
    "",
    "0, GPU-11111111-2222-3333-4444-555555555555, RTX 5090, N/A, 0",
    "0, GPU-11111111-2222-3333-4444-555555555555, RTX 5090, 12",
    "0, not-a-uuid, RTX 5090, 12, 0",
    "0, GPU-11111111-2222-3333-4444-555555555555, RTX 5090, 12, Not Supported",
    "1, GPU-11111111-2222-3333-4444-555555555555, RTX 5090, 12, 0",
    "0, GPU-11111111-2222-3333-4444-555555555555, RTX 5090, 12, 0\n0, GPU-22222222-2222-3333-4444-555555555555, RTX, 1, 0",
])
def test_g7_malformed_target_row_fails_closed(bad_target):
    result = gate.inspect_gpu_admission(
        device_index=0,
        runner=FakeRunner([FakeResult(0, bad_target, "")]),
    )
    assert result["status"] == "blocked_telemetry_unavailable"
    assert gate.admission_exit_code(result) == 2


@pytest.mark.parametrize("bad_compute", [
    "GPU-11111111-2222-3333-4444-555555555555, notapid, python, 512",
    "GPU-11111111-2222-3333-4444-555555555555, 42, python, N/A",
    "GPU-11111111-2222-3333-4444-555555555555, 42, python",
    "GPU-11111111-2222-3333-4444-555555555555, -1, python, 512",
    "GPU-11111111-2222-3333-4444-555555555555, 42, , 512",
    "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee, oops",
])
def test_g8_malformed_compute_row_fails_closed(bad_compute):
    result = gate.inspect_gpu_admission(
        device_index=0,
        runner=FakeRunner([FakeResult(0, _target_row(), ""), FakeResult(0, bad_compute, "")]),
    )
    assert result["status"] == "blocked_telemetry_unavailable"
    assert gate.admission_exit_code(result) == 2


# ---------------------------------------------------------------------------
# G9 — missing nvidia-smi
# ---------------------------------------------------------------------------


def test_g9_missing_nvidia_smi_fails_closed():
    runner = FakeRunner([FileNotFoundError("nvidia-smi")])
    result = gate.inspect_gpu_admission(device_index=0, runner=runner)
    assert result["status"] == "blocked_telemetry_unavailable"
    assert gate.admission_exit_code(result) == 2
    assert result["reason"] == "nvidia_smi_not_found"


def test_g9b_default_runner_missing_executable(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(gate.subprocess, "run", boom)
    result = gate.inspect_gpu_admission(device_index=0)
    assert result["status"] == "blocked_telemetry_unavailable"
    assert gate.admission_exit_code(result) == 2


# ---------------------------------------------------------------------------
# G10 — fixed argv / shell=False / no shell injection
# ---------------------------------------------------------------------------


def test_g10_default_runner_uses_fixed_argv_and_shell_false(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((list(argv), dict(kwargs)))
        if len(seen) == 1:
            return FakeResult(0, _target_row(), "")
        return FakeResult(0, "", "")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    result = gate.inspect_gpu_admission(device_index=0)
    assert result["status"] == "admitted"
    assert len(seen) == 2
    for argv, kwargs in seen:
        assert argv[0] == "nvidia-smi"
        assert kwargs.get("shell") is False
    assert seen[0][0] == [
        "nvidia-smi", "-i", "0",
        "--query-gpu=index,uuid,name,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    assert seen[1][0] == [
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory",
        "--format=csv,noheader,nounits",
    ]


@pytest.mark.parametrize("bad_index", ["0; rm -rf /", "0 && cat /etc/shadow", "-1", "abc", 1.5, True, None])
def test_g10b_rejects_non_integer_or_negative_device_index_before_invocation(bad_index):
    runner = FakeRunner([])
    with pytest.raises((TypeError, ValueError)):
        gate.inspect_gpu_admission(device_index=bad_index, runner=runner)
    assert runner.calls == []


def test_g10c_argv_never_uses_user_passthrough_fields():
    # Query field names are constants; device index is only ever one argv token.
    runner = FakeRunner([FakeResult(0, _target_row(index=2, uuid=TARGET_UUID), ""), FakeResult(0, "", "")])
    gate.inspect_gpu_admission(device_index=2, runner=runner)
    assert runner.calls[0][1:3] == ["-i", "2"]
    assert "--query-gpu=index,uuid,name,memory.used,utilization.gpu" in runner.calls[0]


# ---------------------------------------------------------------------------
# G11 — process-name bounding
# ---------------------------------------------------------------------------


def test_g11_process_name_basename_and_length_bound():
    long_name = "/opt/very/deep/" + ("x" * 500)
    compute = _compute_row(process_name=long_name)
    result, code, _ = _run(compute=compute)
    entry = result["compute_processes"][0]
    assert "/" not in entry["process_name"]
    assert entry["process_name"].startswith("x")
    assert len(entry["process_name"]) <= gate.MAX_PROCESS_NAME_LENGTH
    assert code == 3


def test_g11b_windows_style_separator_is_bounded():
    compute = _compute_row(process_name=r"C:\Users\someone\python.exe")
    result, _, _ = _run(compute=compute)
    assert result["compute_processes"][0]["process_name"] == "python.exe"


def test_g11c_process_evidence_has_only_bounded_keys():
    result, _, _ = _run(compute=_compute_row(process_name="/usr/bin/python3.12", used_memory="2048"))
    entry = result["compute_processes"][0]
    assert set(entry.keys()) == {"gpu_uuid", "pid", "used_gpu_memory_mib", "process_name"}
    assert entry["process_name"] == "python3.12"
    assert entry["used_gpu_memory_mib"] == 2048


# ---------------------------------------------------------------------------
# Structured output / exit codes / CLI
# ---------------------------------------------------------------------------


def test_result_schema_is_bounded_and_stable():
    result, _, _ = _run(compute="")
    assert set(result.keys()) == {
        "schema_version", "status", "device_index", "gpu_uuid", "gpu_name",
        "memory_used_mib", "utilization_gpu_percent", "compute_process_count",
        "compute_processes",
    }
    assert result["schema_version"] == gate.SCHEMA_VERSION
    assert result["status"] in {
        "admitted", "blocked_compute_present", "blocked_telemetry_unavailable",
    }


def test_cli_prints_single_json_object_and_exit_zero(capsys):
    runner = FakeRunner([FakeResult(0, _target_row(), ""), FakeResult(0, "", "")])
    code = gate.main(["--device-index", "0"], runner=runner)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "admitted"
    assert code == 0
    assert out.count("\n") == 1


def test_cli_blocked_compute_exit_three(capsys):
    runner = FakeRunner([FakeResult(0, _target_row(), ""), FakeResult(0, _compute_row(), "")])
    code = gate.main(["--device-index", "0"], runner=runner)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked_compute_present"
    assert code == 3


def test_cli_telemetry_exit_two(capsys):
    runner = FakeRunner([FakeResult(1, "", "boom")])
    code = gate.main(["--device-index", "0"], runner=runner)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked_telemetry_unavailable"
    assert code == 2


def test_cli_optional_output_written_atomically(tmp_path: Path, capsys):
    runner = FakeRunner([FakeResult(0, _target_row(), ""), FakeResult(0, "", "")])
    target = tmp_path / "nested" / "admission.json"
    code = gate.main(["--device-index", "0", "--output", str(target)], runner=runner)
    assert code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["status"] == "admitted"
    assert json.loads(capsys.readouterr().out)["status"] == "admitted"


def test_cli_optional_output_refuses_overwrite(tmp_path: Path, capsys):
    target = tmp_path / "admission.json"
    target.write_text("{}", encoding="utf-8")
    runner = FakeRunner([FakeResult(0, _target_row(), ""), FakeResult(0, "", "")])
    code = gate.main(["--device-index", "0", "--output", str(target)], runner=runner)
    assert code == gate.EXIT_OUTPUT_POLICY
    assert target.read_text(encoding="utf-8") == "{}"


# ---------------------------------------------------------------------------
# G12 — production isolation + no process control in the tool
# ---------------------------------------------------------------------------


def _script_source() -> str:
    return (SCRIPTS / "plan_c_gpu_admission.py").read_text(encoding="utf-8")


def test_g12_no_production_app_imports_the_tool():
    token = "plan_c_gpu_admission"
    offenders = []
    for path in APP.rglob("*.py"):
        if token in path.read_text(encoding="utf-8"):
            offenders.append(str(path))
    assert offenders == []


def test_g12b_no_process_control_tokens_in_tool_source():
    source = _script_source()
    forbidden = re.findall(r"(?<![\w.])(kill|pkill|killall|fuser|systemctl|gpu-reset)(?![\w])", source)
    assert forbidden == []
    assert "os.kill" not in source
    assert "signal" not in source


def test_g12c_tool_does_not_import_torch_or_ultralytics():
    source = _script_source()
    assert "torch" not in source
    assert "ultralytics" not in source

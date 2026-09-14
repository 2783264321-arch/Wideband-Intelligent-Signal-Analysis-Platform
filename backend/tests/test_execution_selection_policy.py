import ast
import pathlib

from app.execution_selection import policy
from app.execution_selection.policy import (
    AUTO_LOCAL_CPU_PREFERRED,
    AUTO_LOCAL_GPU_PREFERRED,
    AUTO_NO_RUNNABLE_EXECUTOR,
    AUTO_ONLY_RUNNABLE_EXECUTOR,
    AUTO_REMOTE_GPU_PREFERRED,
    AUTO_UNKNOWN_DETERMINISTIC_RANK,
    AUTO_UNKNOWN_RECOMMENDED_EXECUTOR,
    GPU_BATCH_ITEMS,
    GPU_PREFER_DURATION_S,
    GPU_PREFER_SAMPLES,
    WorkloadClass,
    classify_workload,
    public_reason_message,
    rank_for,
    select_executor,
)


def test_policy_constants_are_single_sourced():
    assert GPU_PREFER_DURATION_S == 0.05
    assert GPU_PREFER_SAMPLES == 3_000_000
    assert GPU_BATCH_ITEMS == 8


def test_classify_small_when_within_thresholds():
    assert (
        classify_workload(duration_s=0.05, num_samples=3_000_000, dataset_item_count=7)
        is WorkloadClass.SMALL
    )


def test_classify_small_when_all_sizes_absent_is_unknown():
    assert (
        classify_workload(duration_s=None, num_samples=None, dataset_item_count=None)
        is WorkloadClass.UNKNOWN
    )


def test_classify_gpu_beneficial_on_duration():
    assert (
        classify_workload(duration_s=0.0500001, num_samples=None, dataset_item_count=None)
        is WorkloadClass.GPU_BENEFICIAL
    )


def test_classify_gpu_beneficial_on_samples():
    assert (
        classify_workload(duration_s=None, num_samples=3_000_001, dataset_item_count=None)
        is WorkloadClass.GPU_BENEFICIAL
    )


def test_classify_gpu_beneficial_on_dataset_count():
    assert (
        classify_workload(duration_s=None, num_samples=None, dataset_item_count=8)
        is WorkloadClass.GPU_BENEFICIAL
    )


def test_classify_unknown_when_no_size_available():
    assert (
        classify_workload(duration_s=None, num_samples=None, dataset_item_count=None)
        is WorkloadClass.UNKNOWN
    )


def test_rank_for_small():
    assert rank_for(WorkloadClass.SMALL) == ("local_cpu", "local_gpu", "remote_gpu")


def test_rank_for_gpu_beneficial():
    assert rank_for(WorkloadClass.GPU_BENEFICIAL) == ("local_gpu", "remote_gpu", "local_cpu")


def test_rank_for_unknown_fallback():
    assert rank_for(WorkloadClass.UNKNOWN) == ("local_gpu", "local_cpu", "remote_gpu")


def test_no_runnable_is_fail_closed():
    decision = select_executor(
        runnable=(), workload_class=WorkloadClass.SMALL, recommended_execution=None
    )
    assert decision.resolved_executor is None
    assert decision.reason_code == AUTO_NO_RUNNABLE_EXECUTOR
    assert decision.workload_class is WorkloadClass.SMALL


def test_only_runnable_is_selected():
    decision = select_executor(
        runnable=("remote_gpu",),
        workload_class=WorkloadClass.SMALL,
        recommended_execution=None,
    )
    assert decision.resolved_executor == "remote_gpu"
    assert decision.reason_code == AUTO_ONLY_RUNNABLE_EXECUTOR


def test_small_ranking_prefers_local_cpu():
    decision = select_executor(
        runnable=("local_cpu", "local_gpu", "remote_gpu"),
        workload_class=WorkloadClass.SMALL,
        recommended_execution="local_gpu",
    )
    assert decision.resolved_executor == "local_cpu"
    assert decision.reason_code == AUTO_LOCAL_CPU_PREFERRED


def test_gpu_beneficial_ranking_prefers_local_gpu():
    decision = select_executor(
        runnable=("local_cpu", "local_gpu", "remote_gpu"),
        workload_class=WorkloadClass.GPU_BENEFICIAL,
        recommended_execution="local_cpu",
    )
    assert decision.resolved_executor == "local_gpu"
    assert decision.reason_code == AUTO_LOCAL_GPU_PREFERRED


def test_gpu_beneficial_ranking_remote_over_cpu():
    decision = select_executor(
        runnable=("local_cpu", "remote_gpu"),
        workload_class=WorkloadClass.GPU_BENEFICIAL,
        recommended_execution=None,
    )
    assert decision.resolved_executor == "remote_gpu"
    assert decision.reason_code == AUTO_REMOTE_GPU_PREFERRED


def test_unknown_uses_recommended_when_runnable():
    decision = select_executor(
        runnable=("local_cpu", "local_gpu"),
        workload_class=WorkloadClass.UNKNOWN,
        recommended_execution="local_gpu",
    )
    assert decision.resolved_executor == "local_gpu"
    assert decision.reason_code == AUTO_UNKNOWN_RECOMMENDED_EXECUTOR


def test_unknown_ignores_recommended_when_not_runnable():
    decision = select_executor(
        runnable=("local_cpu", "local_gpu"),
        workload_class=WorkloadClass.UNKNOWN,
        recommended_execution="remote_gpu",
    )
    assert decision.resolved_executor == "local_gpu"  # UNKNOWN fallback rank
    assert decision.reason_code == AUTO_UNKNOWN_DETERMINISTIC_RANK


def test_public_reason_message_covers_auto_codes():
    assert public_reason_message(AUTO_NO_RUNNABLE_EXECUTOR)
    assert public_reason_message(AUTO_ONLY_RUNNABLE_EXECUTOR)
    assert public_reason_message(AUTO_LOCAL_CPU_PREFERRED)
    assert public_reason_message(AUTO_LOCAL_GPU_PREFERRED)
    assert public_reason_message(AUTO_REMOTE_GPU_PREFERRED)
    assert public_reason_message(AUTO_UNKNOWN_RECOMMENDED_EXECUTOR)
    assert public_reason_message(AUTO_UNKNOWN_DETERMINISTIC_RANK)


def test_public_reason_message_covers_availability_codes():
    assert public_reason_message("EXECUTION_CAPABILITY_UNAVAILABLE")
    assert public_reason_message("EXECUTION_NOT_CERTIFIED")
    assert public_reason_message("INPUT_INCOMPATIBLE")
    assert public_reason_message("REMOTE_EXECUTOR_UNAVAILABLE")


def test_capability_unavailable_public_message_does_not_imply_unconfigured():
    # EXECUTION_CAPABILITY_UNAVAILABLE is broader than "not configured": the
    # platform also uses it when a provider IS configured but its live health
    # probe fails. The public projection must therefore stay generic.
    message = public_reason_message("EXECUTION_CAPABILITY_UNAVAILABLE")
    assert message == "Executor is currently unavailable."
    assert "not configured" not in message.lower()


def test_public_reason_message_is_platform_owned_and_generic_for_unknown():
    assert public_reason_message("SOME_UNKNOWN_CODE") == "Executor is currently unavailable."
    # Defensive fallback only: the resolver NEVER calls this for an available
    # candidate (an available candidate's message is None by resolver contract).
    assert public_reason_message(None) == "Executor is currently unavailable."


def test_public_reason_message_texts_are_bounded_and_path_free():
    codes = [
        AUTO_NO_RUNNABLE_EXECUTOR,
        AUTO_ONLY_RUNNABLE_EXECUTOR,
        AUTO_LOCAL_CPU_PREFERRED,
        AUTO_LOCAL_GPU_PREFERRED,
        AUTO_REMOTE_GPU_PREFERRED,
        AUTO_UNKNOWN_RECOMMENDED_EXECUTOR,
        AUTO_UNKNOWN_DETERMINISTIC_RANK,
        "EXECUTION_CAPABILITY_UNAVAILABLE",
        "EXECUTION_NOT_CERTIFIED",
        "INPUT_INCOMPATIBLE",
        "REMOTE_EXECUTOR_UNAVAILABLE",
        "TOTALLY_UNKNOWN",
    ]
    for code in codes:
        message = public_reason_message(code)
        assert isinstance(message, str)
        assert 0 < len(message) <= 200
        for forbidden in ("\\", "/", ".exe", ".pt", "C:", "/root", "ssh"):
            assert forbidden not in message


def test_policy_module_has_no_linux_or_ml_dependency():
    source = pathlib.Path(policy.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert imported <= {"dataclasses", "enum", "typing"}
    lowered = source.lower()
    for forbidden in ("cgroup", "nvidia-smi", "bhq3_memory_gate", "/proc", "autodl", "subprocess", "ultralytics"):
        assert forbidden not in lowered
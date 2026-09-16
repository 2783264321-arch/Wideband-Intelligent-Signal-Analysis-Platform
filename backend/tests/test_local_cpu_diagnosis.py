"""Local CPU configuration predicate tests (durable C1 diagnostic evidence).

These tests pin the FAIL-CLOSED configuration predicate. They are NOT the
acceptance proof that Local CPU works on a given host: acceptance requires the
operator qualification workflow (runtime doctor -> qualify -> certificate
install -> registry rebuild -> four-predicate verification -> one CPU-only
AnalysisRun). See the UX-C plan.
"""
from app.analysis.local_executor import build_local_providers


def test_local_cpu_absent_when_interpreter_and_ref_unset(settings):
    # Default control-plane settings have no separate ML interpreter configured.
    assert "local_cpu" not in build_local_providers(settings)


def test_local_cpu_registered_when_interpreter_and_ref_configured(settings, tmp_path):
    settings.local_cpu_python_path = tmp_path / "python.exe"
    settings.local_cpu_runtime_ref = "local:test:cpu:0123456789ab"
    providers = build_local_providers(settings)
    assert "local_cpu" in providers
    assert providers["local_cpu"].runtime_ref == "local:test:cpu:0123456789ab"

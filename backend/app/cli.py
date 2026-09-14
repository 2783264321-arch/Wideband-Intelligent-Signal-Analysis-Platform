"""A3 Task 6: operator CLI (stdlib argparse).

    wisa runtime doctor
    wisa qualify --plugin <id> [--plugin-version <v>] --executor <e> [--model-release <id>]
    wisa certificate install --from <evidence_dir>
    wisa certificate list

Also runnable as ``python -m app.cli``. No HTTP API, no frontend.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

from app.analysis.local_executor import build_local_providers
from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.registry import create_pipeline_registry
from app.remote_execution.model_release import ModelReleaseStore, load_model_release_defaults
from app.remote_execution.runtime import ExecutorRegistry
from app.runtime_qualification import doctor as doctor_module
from app.runtime_qualification import identity as identity_module
from app.runtime_qualification.evidence import load_evidence_dir, write_evidence
from app.runtime_qualification.install import (
    build_certificate_store,
    install_certificate,
    load_operator_certificates,
    load_execution_certificates,
    resolve_live_authority,
)
from app.runtime_qualification.qualification import (
    QualificationTarget,
    build_default_target_probe,
    run_qualification,
    select_runner,
)

_EXECUTORS = ("local_cpu", "local_gpu", "remote_gpu")


@dataclass
class CliContext:
    settings: Settings
    pipeline_registry: object
    model_release_store: object
    executor_registry: object
    repo_certificate_path: Path
    data_root: Path


def _repo_certificate_path() -> Path:
    return Path(__file__).resolve().parent / "pipelines" / "execution_certificates.json"


def _default_context(settings: Settings) -> CliContext:
    plugins_root = Path(__file__).resolve().parent / "pipelines"
    model_release_store = ModelReleaseStore(
        plugins_root, load_model_release_defaults(plugins_root / "model_release_defaults.json")
    )
    repo_path = _repo_certificate_path()
    providers = dict(build_local_providers(settings))
    executor_registry = ExecutorRegistry(
        providers, build_certificate_store(repo_path=repo_path, data_root=settings.data_root)
    )
    return CliContext(
        settings=settings,
        pipeline_registry=create_pipeline_registry(),
        model_release_store=model_release_store,
        executor_registry=executor_registry,
        repo_certificate_path=repo_path,
        data_root=settings.data_root,
    )


def _providers_of(executor_registry: object) -> dict:
    getter = getattr(executor_registry, "providers", None)
    if callable(getter):
        return dict(getter())
    return dict(getattr(executor_registry, "_providers", {}) or {})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wisa")
    sub = parser.add_subparsers(dest="command")

    runtime = sub.add_parser("runtime")
    runtime_sub = runtime.add_subparsers(dest="runtime_command")
    runtime_sub.add_parser("doctor")

    qualify = sub.add_parser("qualify")
    qualify.add_argument("--plugin", required=True)
    qualify.add_argument("--plugin-version", default=None)
    qualify.add_argument("--executor", required=True, choices=_EXECUTORS)
    qualify.add_argument("--model-release", default=None)

    certificate = sub.add_parser("certificate")
    certificate_sub = certificate.add_subparsers(dest="certificate_command")
    install = certificate_sub.add_parser("install")
    install.add_argument("--from", dest="from_dir", required=True)
    certificate_sub.add_parser("list")

    return parser


def _cmd_runtime_doctor(ctx: CliContext, out) -> int:
    repo_refs = frozenset(
        certificate.runtime_ref for certificate in load_execution_certificates(ctx.repo_certificate_path)
    )

    def scheme_for(executor: str, runtime_ref: str) -> str:
        return identity_module.resolve_identity_scheme(
            executor=executor,
            runtime_ref=runtime_ref,
            qualification_context=None,
            repo_default_runtime_refs=repo_refs,
            material_available=True,
        )

    specs = doctor_module.build_provider_specs(
        ctx.settings, _providers_of(ctx.executor_registry), scheme_for=scheme_for
    )
    report = doctor_module.build_runtime_doctor_report(
        settings=ctx.settings,
        provider_specs=specs,
        interpreter_probe=doctor_module.SubprocessInterpreterProbe(),
        gpu_probe=doctor_module.NvidiaSmiGpuProbe(),
    )
    print(json.dumps(report.to_operator_json(), indent=2), file=out)
    return 0


def _cmd_qualify(ctx: CliContext, args, out) -> int:
    definition = ctx.pipeline_registry.get(args.plugin).definition
    if args.plugin_version is not None and args.plugin_version != definition.plugin_version:
        raise PlatformError("PLUGIN_NOT_FOUND", "Requested plugin version is not registered.")

    if definition.model_release_required:
        resolved = ctx.model_release_store.resolve(
            args.plugin, definition.plugin_version, args.model_release
        )
        model_release_id: str | None = resolved.release.model_release_id
    else:
        if args.model_release is not None:
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH",
                "Release-less plugin must not carry a model release identity.",
            )
        model_release_id = None

    provider = _providers_of(ctx.executor_registry).get(args.executor)
    if provider is None:
        raise PlatformError(
            "EXECUTION_CAPABILITY_UNAVAILABLE",
            f"No executor provider is registered for '{args.executor}'.",
        )
    target = QualificationTarget(
        plugin_id=args.plugin,
        plugin_version=definition.plugin_version,
        model_release_id=model_release_id,
        executor=args.executor,
        runtime_ref=provider.runtime_ref,
        runtime_descriptor=provider.runtime_descriptor().to_metadata(),
    )

    if args.executor == "local_cpu":
        probe = build_default_target_probe(
            target=target,
            settings=ctx.settings,
            pipeline_registry=ctx.pipeline_registry,
            model_release_store=ctx.model_release_store,
            executor_registry=ctx.executor_registry,
        )
        runner = select_runner(executor=args.executor, target_probe=probe)
    else:
        runner = select_runner(executor=args.executor, target_probe=None)

    evidence = run_qualification(target=target, runner=runner)
    path = write_evidence(ctx.data_root, evidence)
    print(
        json.dumps(
            {
                "passed": evidence.passed,
                "qualification_type": evidence.qualification_type,
                "identity_scheme": evidence.identity_scheme,
                "runtime_ref": evidence.runtime_ref,
                "evidence": str(path),
                "results": [
                    {"name": result.name, "passed": result.passed, "detail": result.detail}
                    for result in evidence.results
                ],
            },
            indent=2,
        ),
        file=out,
    )
    return 0 if evidence.passed else 1


def _cmd_certificate_install(ctx: CliContext, args, out) -> int:
    evidence = load_evidence_dir(Path(args.from_dir))
    authority = resolve_live_authority(
        registry=ctx.pipeline_registry,
        model_release_store=ctx.model_release_store,
        executor_registry=ctx.executor_registry,
        plugin_id=evidence.plugin_id,
        plugin_version=evidence.plugin_version,
        executor=evidence.executor,
        requested_model_release_id=evidence.model_release_id,
        data_root=ctx.data_root,
    )
    result = install_certificate(
        data_root=ctx.data_root,
        repo_certificate_path=ctx.repo_certificate_path,
        evidence=evidence,
        authority=authority,
    )
    print(json.dumps({"status": result.status, "certificate": result.certificate.__dict__}, indent=2), file=out)
    print(
        "certificate becomes effective on next control-plane restart / registry rebuild",
        file=out,
    )
    return 0


def _cmd_certificate_list(ctx: CliContext, out) -> int:
    certificates = load_operator_certificates(ctx.data_root)
    print(json.dumps([certificate.__dict__ for certificate in certificates], indent=2), file=out)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    context_factory=None,
    stdout=None,
    stderr=None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        ctx = (context_factory or _default_context)(Settings())
        if args.command == "runtime" and args.runtime_command == "doctor":
            return _cmd_runtime_doctor(ctx, out)
        if args.command == "qualify":
            return _cmd_qualify(ctx, args, out)
        if args.command == "certificate" and args.certificate_command == "install":
            return _cmd_certificate_install(ctx, args, out)
        if args.command == "certificate" and args.certificate_command == "list":
            return _cmd_certificate_list(ctx, out)
    except PlatformError as exc:
        print(f"{exc.code}: {exc.message}", file=err)
        return 1
    parser.print_usage(err)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

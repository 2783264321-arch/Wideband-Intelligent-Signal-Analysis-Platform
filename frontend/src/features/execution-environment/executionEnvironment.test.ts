import type { ExecutionCandidate, ExecutorSelection } from "../../api/types";
import {
  EXECUTOR_OPTIONS,
  autoOptionState,
  effectiveSelectionForScope,
  optionStateFromCandidate,
  optionsFromSelection,
  scopeKeyFor,
} from "./executionEnvironment";

function candidate(overrides: Partial<ExecutionCandidate> & { executor: string }): ExecutionCandidate {
  return {
    executor: overrides.executor,
    technical: overrides.technical ?? true,
    configured: overrides.configured ?? true,
    certified: overrides.certified ?? true,
    available: overrides.available ?? true,
    reasonCode: overrides.reasonCode ?? null,
    reasonMessage: overrides.reasonMessage ?? null,
  };
}

function selection(overrides: Partial<ExecutorSelection> = {}): ExecutorSelection {
  return {
    requestedMode: overrides.requestedMode ?? "auto",
    resolvedExecutor: "resolvedExecutor" in overrides ? (overrides.resolvedExecutor ?? null) : "local_cpu",
    reasonCode: overrides.reasonCode ?? "AUTO_ONLY_RUNNABLE_EXECUTOR",
    reason: overrides.reason ?? "Only local_cpu is runnable.",
    workloadClass: overrides.workloadClass ?? "SMALL",
    candidates: overrides.candidates ?? [candidate({ executor: "local_cpu" })],
  };
}

test("the four product options are a fixed vocabulary, always enumerated", () => {
  expect(EXECUTOR_OPTIONS).toEqual(["auto", "local_cpu", "local_gpu", "remote_gpu"]);
  const options = optionsFromSelection(null);
  expect(options.map((option) => option.key)).toEqual(["auto", "local_cpu", "local_gpu", "remote_gpu"]);
});

test("a manual option is NOT hidden merely because it is absent from candidates", () => {
  const options = optionsFromSelection(selection({ candidates: [candidate({ executor: "local_cpu" })] }));
  expect(options.map((option) => option.key)).toEqual(["auto", "local_cpu", "local_gpu", "remote_gpu"]);
  const localGpu = options.find((option) => option.key === "local_gpu");
  expect(localGpu?.enabled).toBe(false);
  expect(localGpu?.state).toBe("unsupported");
});

test("candidate facts map to the exact backend-derived states", () => {
  expect(optionStateFromCandidate(candidate({ executor: "local_cpu", technical: false })).state).toBe("unsupported");
  expect(
    optionStateFromCandidate(candidate({ executor: "local_cpu", technical: true, configured: false })).state,
  ).toBe("not_configured");
  expect(
    optionStateFromCandidate(
      candidate({ executor: "local_cpu", technical: true, configured: true, certified: false }),
    ).state,
  ).toBe("not_certified");
  expect(
    optionStateFromCandidate(
      candidate({ executor: "local_cpu", technical: true, configured: true, certified: true, available: false }),
    ).state,
  ).toBe("temporarily_unavailable");
  expect(optionStateFromCandidate(candidate({ executor: "local_cpu" })).state).toBe("available");
});

test("only an available manual option is enabled", () => {
  expect(optionStateFromCandidate(candidate({ executor: "local_cpu" })).enabled).toBe(true);
  expect(
    optionStateFromCandidate(candidate({ executor: "local_cpu", available: false, reasonCode: "EXECUTION_CAPABILITY_UNAVAILABLE", reasonMessage: "probe failed" })).enabled,
  ).toBe(false);
  const disabled = optionStateFromCandidate(
    candidate({ executor: "remote_gpu", configured: false, reasonCode: "EXECUTION_CAPABILITY_UNAVAILABLE", reasonMessage: "not registered" }),
  );
  expect(disabled.enabled).toBe(false);
  expect(disabled.reasonCode).toBe("EXECUTION_CAPABILITY_UNAVAILABLE");
  expect(disabled.reasonMessage).toBe("not registered");
});

test("optionsFromSelection maps each candidate to its option", () => {
  const options = optionsFromSelection(selection({
    candidates: [
      candidate({ executor: "local_cpu" }),
      candidate({ executor: "remote_gpu", configured: false }),
    ],
  }));
  const byKey = Object.fromEntries(options.map((option) => [option.key, option]));
  expect(byKey.local_cpu.state).toBe("available");
  expect(byKey.local_cpu.enabled).toBe(true);
  expect(byKey.remote_gpu.state).toBe("not_configured");
  expect(byKey.remote_gpu.enabled).toBe(false);
  expect(byKey.local_gpu.state).toBe("unsupported");
});

test("Auto is enabled only when the backend resolved an executor", () => {
  const resolved = autoOptionState(selection({ resolvedExecutor: "local_gpu", reasonCode: "AUTO_LOCAL_GPU_PREFERRED", reason: "GPU beneficial." }));
  expect(resolved.key).toBe("auto");
  expect(resolved.executor).toBeNull();
  expect(resolved.enabled).toBe(true);
  expect(resolved.state).toBe("available");
  expect(resolved.reasonCode).toBe("AUTO_LOCAL_GPU_PREFERRED");
  expect(resolved.reasonMessage).toBe("GPU beneficial.");
});

test("Auto is unresolved (never a fallback) when no executor is runnable", () => {
  const unresolved = autoOptionState(selection({
    resolvedExecutor: null,
    reasonCode: "AUTO_NO_RUNNABLE_EXECUTOR",
    reason: "No runnable executor.",
    candidates: [],
  }));
  expect(unresolved.enabled).toBe(false);
  expect(unresolved.state).toBe("unresolved");
  expect(unresolved.reasonCode).toBe("AUTO_NO_RUNNABLE_EXECUTOR");
});

test("Auto is unresolved while selection is still loading (null)", () => {
  const unresolved = autoOptionState(null);
  expect(unresolved.enabled).toBe(false);
  expect(unresolved.state).toBe("unresolved");
});

test("scopeKeyFor distinguishes recording and pipeline identity", () => {
  expect(scopeKeyFor("rec_1", "pA")).not.toBe(scopeKeyFor("rec_1", "pB"));
  expect(scopeKeyFor("rec_1", "pA")).not.toBe(scopeKeyFor("rec_2", "pA"));
});

test("a selection bound to another scope can never authorize the current scope", () => {
  const bound = { scopeKey: scopeKeyFor("rec_1", "pA"), value: selection() };
  expect(effectiveSelectionForScope(bound, scopeKeyFor("rec_1", "pB"))).toBeNull();
  expect(effectiveSelectionForScope(bound, scopeKeyFor("rec_2", "pA"))).toBeNull();
  expect(effectiveSelectionForScope(bound, scopeKeyFor("rec_1", "pA"))).not.toBeNull();
  expect(effectiveSelectionForScope(null, scopeKeyFor("rec_1", "pA"))).toBeNull();
});

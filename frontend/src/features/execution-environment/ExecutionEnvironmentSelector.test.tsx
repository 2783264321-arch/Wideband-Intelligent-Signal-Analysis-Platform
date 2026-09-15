import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { ExecutionEnvironmentSelector } from "./ExecutionEnvironmentSelector";
import { LocalizationProvider } from "../../localization/LocalizationProvider";

function LocaleWrap({ children }: { children: ReactNode }) {
  return <LocalizationProvider initialLocale="en-US">{children}</LocalizationProvider>;
}
import type { ExecutionCandidate, ExecutorSelection } from "../../api/types";

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

const AUTO_VALUE = { mode: "auto" as const, executor: null };

test("renders all four product options regardless of candidates", () => {
  render(
    <LocaleWrap>
    <ExecutionEnvironmentSelector
      selection={selection({ candidates: [candidate({ executor: "local_cpu" })] })}
      loading={false}
      error={null}
      value={AUTO_VALUE}
      onChange={() => {}}
    />
    </LocaleWrap>,
  );
  expect(screen.getByText("Auto")).toBeInTheDocument();
  expect(screen.getByText("Local CPU")).toBeInTheDocument();
  expect(screen.getByText("Local GPU")).toBeInTheDocument();
  expect(screen.getByText("Remote GPU")).toBeInTheDocument();
});

test("Auto shows the backend-resolved executor (recommended), never a client decision", () => {
  render(
    <LocaleWrap>
    <ExecutionEnvironmentSelector
      selection={selection({ resolvedExecutor: "local_gpu", reasonCode: "AUTO_LOCAL_GPU_PREFERRED" })}
      loading={false}
      error={null}
      value={AUTO_VALUE}
      onChange={() => {}}
    />
    </LocaleWrap>,
  );
  expect(screen.getByTestId("execution-environment-summary")).toHaveTextContent("Recommended: Local GPU");
});

test("selecting a manual option emits the exact executor", () => {
  const onChange = vi.fn();
  render(
    <LocaleWrap>
    <ExecutionEnvironmentSelector
      selection={selection({ candidates: [candidate({ executor: "local_cpu" })] })}
      loading={false}
      error={null}
      value={AUTO_VALUE}
      onChange={onChange}
    />
    </LocaleWrap>,
  );
  fireEvent.click(screen.getByText("Local CPU"));
  expect(onChange).toHaveBeenCalledWith({ mode: "manual", executor: "local_cpu" });
});

test("selecting Auto emits the auto mode (not a resolved executor)", () => {
  const onChange = vi.fn();
  render(
    <LocaleWrap>
    <ExecutionEnvironmentSelector
      selection={selection()}
      loading={false}
      error={null}
      value={{ mode: "manual", executor: "local_cpu" }}
      onChange={onChange}
    />
    </LocaleWrap>,
  );
  fireEvent.click(screen.getByText("Auto"));
  expect(onChange).toHaveBeenCalledWith({ mode: "auto", executor: null });
});

test("an unavailable manual option is disabled and shows its bounded reason in details", () => {
  const onChange = vi.fn();
  render(
    <LocaleWrap>
    <ExecutionEnvironmentSelector
      selection={selection({
        candidates: [
          candidate({ executor: "local_cpu" }),
          candidate({
            executor: "remote_gpu",
            configured: false,
            reasonCode: "EXECUTION_CAPABILITY_UNAVAILABLE",
            reasonMessage: "No executor provider is registered for 'remote_gpu'.",
          }),
        ],
      })}
      loading={false}
      error={null}
      value={AUTO_VALUE}
      onChange={onChange}
    />
    </LocaleWrap>,
  );
  fireEvent.click(screen.getByText("Remote GPU"));
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /details/i }));
  expect(screen.getByText(/No executor provider is registered/)).toBeInTheDocument();
  expect(screen.getByTestId("execution-option-remote_gpu")).toHaveTextContent(/Not configured|未配置/);
});

test("Auto is unresolved when no executor is runnable and does NOT fall back", () => {
  render(
    <LocaleWrap>
    <ExecutionEnvironmentSelector
      selection={selection({
        resolvedExecutor: null,
        reasonCode: "AUTO_NO_RUNNABLE_EXECUTOR",
        reason: "No runnable executor.",
        candidates: [],
      })}
      loading={false}
      error={null}
      value={AUTO_VALUE}
      onChange={() => {}}
    />
    </LocaleWrap>,
  );
  const auto = screen.getAllByRole("radio").find((element) => element.closest("label")?.textContent === "Auto");
  expect(auto).toBeDefined();
  expect(auto).toBeDisabled();
  expect(screen.getByTestId("execution-environment-summary")).toHaveTextContent("No runnable executor.");
});

test("backend facts drive availability: certified-but-unavailable is disabled", () => {
  render(
    <LocaleWrap>
    <ExecutionEnvironmentSelector
      selection={selection({
        candidates: [candidate({ executor: "local_gpu", available: false, reasonCode: "EXECUTION_CAPABILITY_UNAVAILABLE" })],
      })}
      loading={false}
      error={null}
      value={AUTO_VALUE}
      onChange={() => {}}
    />
    </LocaleWrap>,
  );
  const gpu = screen.getAllByRole("radio").find((element) => element.closest("label")?.textContent === "Local GPU");
  expect(gpu).toBeDisabled();
});

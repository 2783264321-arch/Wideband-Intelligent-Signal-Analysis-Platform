import { fireEvent, render, screen } from "@testing-library/react";
import { ExecutionEnvironmentSelector } from "./ExecutionEnvironmentSelector";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
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

function openSelect() {
  fireEvent.mouseDown(screen.getByLabelText("Execution Environment"));
}

async function chooseOption(name: string) {
  const options = await screen.findAllByTitle(name);
  fireEvent.click(options[options.length - 1]);
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("happy path renders a single compact Auto · Local CPU control, not radio buttons", () => {
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={selection({ candidates: [candidate({ executor: "local_cpu" })] })}
        loading={false}
        error={null}
        value={AUTO_VALUE}
        onChange={() => {}}
      />,
    ),
  );
  expect(screen.getByText("Auto · Local CPU")).toBeInTheDocument();
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  // Unavailable executors must not appear as primary controls.
  expect(screen.queryByText("Local GPU")).toBeNull();
  expect(screen.queryByText("Remote GPU")).toBeNull();
});

test("localizes the compact control in zh-CN", () => {
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={selection({ candidates: [candidate({ executor: "local_cpu" })] })}
        loading={false}
        error={null}
        value={AUTO_VALUE}
        onChange={() => {}}
      />,
      { locale: "zh-CN" },
    ),
  );
  expect(screen.getByText("自动 · 本地 CPU")).toBeInTheDocument();
});

test("dropdown offers only Auto and runnable manual executors", async () => {
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={selection({
          candidates: [
            candidate({ executor: "local_cpu" }),
            candidate({ executor: "local_gpu", technical: false, configured: false, certified: false, available: false }),
            candidate({ executor: "remote_gpu", technical: false, configured: false, certified: false, available: false }),
          ],
        })}
        loading={false}
        error={null}
        value={AUTO_VALUE}
        onChange={() => {}}
      />,
    ),
  );
  openSelect();
  expect(await screen.findAllByTitle("Auto · Local CPU")).not.toHaveLength(0);
  expect(await screen.findAllByTitle("Local CPU")).not.toHaveLength(0);
  expect(screen.queryByTitle("Local GPU")).toBeNull();
  expect(screen.queryByTitle("Remote GPU")).toBeNull();
});

test("selecting the runnable manual Local CPU preserves manual request semantics", async () => {
  const onChange = vi.fn();
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={selection({ candidates: [candidate({ executor: "local_cpu" })] })}
        loading={false}
        error={null}
        value={AUTO_VALUE}
        onChange={onChange}
      />,
    ),
  );
  openSelect();
  await chooseOption("Local CPU");
  expect(onChange).toHaveBeenCalledWith({ mode: "manual", executor: "local_cpu" });
});

test("selecting Auto preserves mode=auto and never emits a resolved executor", async () => {
  const onChange = vi.fn();
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={selection({ candidates: [candidate({ executor: "local_cpu" })] })}
        loading={false}
        error={null}
        value={{ mode: "manual", executor: "local_cpu" }}
        onChange={onChange}
      />,
    ),
  );
  openSelect();
  await chooseOption("Auto · Local CPU");
  expect(onChange).toHaveBeenCalledWith({ mode: "auto", executor: null });
});

test("unavailable executors are disclosed only after opening Environment details", () => {
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={selection({
          candidates: [
            candidate({ executor: "local_cpu" }),
            candidate({ executor: "local_gpu", technical: false, configured: false, certified: false, available: false, reasonCode: "EXECUTION_CAPABILITY_UNAVAILABLE" }),
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
        onChange={() => {}}
      />,
    ),
  );
  expect(screen.queryByTestId("execution-environment-details")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Environment details" }));
  const localGpu = screen.getByTestId("execution-option-local_gpu");
  expect(localGpu).toHaveTextContent("Local GPU");
  expect(localGpu).toHaveTextContent("Unsupported");
  const remoteGpu = screen.getByTestId("execution-option-remote_gpu");
  expect(remoteGpu).toHaveTextContent("Not configured");
  expect(remoteGpu).toHaveTextContent("No executor provider is registered");
  // Technical reason code is only behind the disclosure.
  expect(screen.getByTestId("execution-environment-reason-code")).toHaveTextContent("AUTO_ONLY_RUNNABLE_EXECUTOR");
});

test("loading shows a checking state instead of disabled executor buttons", () => {
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={null}
        loading
        error={null}
        value={AUTO_VALUE}
        onChange={() => {}}
      />,
    ),
  );
  expect(screen.getByText("Checking local environment…")).toBeInTheDocument();
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(screen.getByLabelText("Execution Environment")).toBeDisabled();
});

test("no-runnable state is concise and keeps details accessible", () => {
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        compact
        selection={selection({
          resolvedExecutor: null,
          reasonCode: "AUTO_NO_RUNNABLE_EXECUTOR",
          reason: "No runnable executor.",
          candidates: [candidate({ executor: "local_cpu", configured: false, certified: false, available: false })],
        })}
        loading={false}
        error={null}
        value={AUTO_VALUE}
        onChange={() => {}}
      />,
    ),
  );
  // Compact mode removes the standing banner; the reason stays one click away
  // behind the why-link.
  expect(screen.queryByTestId("execution-environment-no-runnable")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Why can't this pipeline run/i }));
  expect(screen.getByTestId("execution-option-local_cpu")).toHaveTextContent("Not configured");
});

test("non-compact mode keeps the standing not-runnable banner visible", () => {
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={selection({
          resolvedExecutor: null,
          reasonCode: "AUTO_NO_RUNNABLE_EXECUTOR",
          reason: "No runnable executor.",
          candidates: [candidate({ executor: "local_cpu", configured: false, certified: false, available: false })],
        })}
        loading={false}
        error={null}
        value={AUTO_VALUE}
        onChange={() => {}}
      />,
    ),
  );
  expect(screen.getByTestId("execution-environment-no-runnable")).toHaveTextContent("This algorithm cannot run right now.");
});

test("a certified-but-unavailable manual executor is not offered and never falls back", () => {
  const onChange = vi.fn();
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={selection({
          resolvedExecutor: null,
          reasonCode: "AUTO_NO_RUNNABLE_EXECUTOR",
          reason: "No runnable executor.",
          candidates: [candidate({ executor: "local_gpu", available: false, reasonCode: "EXECUTION_CAPABILITY_UNAVAILABLE" })],
        })}
        loading={false}
        error={null}
        value={AUTO_VALUE}
        onChange={onChange}
      />,
    ),
  );
  openSelect();
  expect(screen.queryByTitle("Local GPU")).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});

test("renders the backend error and locks the control", () => {
  render(
    renderWithLocalization(
      <ExecutionEnvironmentSelector
        selection={null}
        loading={false}
        error="Unable to load execution environments."
        value={AUTO_VALUE}
        onChange={() => {}}
      />,
    ),
  );
  expect(screen.getByTestId("execution-environment-error")).toHaveTextContent("Unable to load execution environments.");
  expect(screen.getByLabelText("Execution Environment")).toBeDisabled();
});

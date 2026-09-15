import { render, screen } from "@testing-library/react";
import { RunStatusBadge } from "./RunStatusBadge";
import { LocalizationProvider } from "../../localization/LocalizationProvider";

function renderWithLocale(ui: React.ReactElement, locale: "zh-CN" | "en-US" = "en-US") {
  return render(<LocalizationProvider initialLocale={locale}>{ui}</LocalizationProvider>);
}

test("maps every AnalysisRun status to its label (en-US)", () => {
  const cases: Array<[string, string]> = [
    ["pending", "Pending"],
    ["running", "Running"],
    ["completed", "Completed"],
    ["failed", "Failed"],
    ["interrupted", "Interrupted"],
  ];
  for (const [status, label] of cases) {
    const { unmount } = renderWithLocale(<RunStatusBadge status={status} errorType={null} errorMessage={null} />);
    expect(screen.getByTestId("run-status-badge")).toHaveTextContent(label);
    unmount();
  }
});

test("zh-CN renders the localized status and keeps the raw code", () => {
  renderWithLocale(
    <RunStatusBadge status="interrupted" errorType="ANALYSIS_LAUNCH_AMBIGUOUS" errorMessage="stale intent" />,
    "zh-CN",
  );
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("已中断");
  expect(badge).not.toHaveTextContent("失败");
  expect(screen.getByTestId("run-error-code")).toHaveTextContent("ANALYSIS_LAUNCH_AMBIGUOUS");
  expect(badge).toHaveTextContent("stale intent");
});

test("unknown status falls back to the raw backend status verbatim", () => {
  renderWithLocale(<RunStatusBadge status="mystery_state" errorType={null} errorMessage={null} />);
  expect(screen.getByTestId("run-status-badge")).toHaveTextContent("mystery_state");
});

test("failed preserves the bounded error code", () => {
  renderWithLocale(<RunStatusBadge status="failed" errorType="INPUT_INCOMPATIBLE" errorMessage="Recording label space is not accepted." />);
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("Failed");
  expect(badge).toHaveTextContent("INPUT_INCOMPATIBLE");
  expect(badge).toHaveTextContent("Recording label space is not accepted.");
});

test("interrupted launch-ambiguous renders Interrupted (not Failed) with the bounded code", () => {
  renderWithLocale(<RunStatusBadge status="interrupted" errorType="ANALYSIS_LAUNCH_AMBIGUOUS" errorMessage="Launch intent was durable but no worker started." />);
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("Interrupted");
  expect(badge).not.toHaveTextContent("Failed");
  expect(badge).toHaveTextContent("ANALYSIS_LAUNCH_AMBIGUOUS");
  expect(badge).toHaveTextContent("Launch intent was durable but no worker started.");
});

test("a terminal run with no errorType fabricates no error code", () => {
  renderWithLocale(<RunStatusBadge status="completed" errorType={null} errorMessage={null} />);
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("Completed");
  expect(screen.queryByTestId("run-error-code")).toBeNull();
});

test("a running run shows only its label when there is no error", () => {
  renderWithLocale(<RunStatusBadge status="running" errorType={null} errorMessage={null} />);
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("Running");
  expect(screen.queryByTestId("run-error-code")).toBeNull();
});

// ---------------------------------------------------------------------------
// Post-freeze review fix — known/unknown code detail handling
// ---------------------------------------------------------------------------

test("known code keeps raw code + localized explanation + technical detail (en-US)", () => {
  renderWithLocale(
    <RunStatusBadge status="failed" errorType="INPUT_INCOMPATIBLE" errorMessage="Recording label space mismatch." />,
    "en-US",
  );
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("INPUT_INCOMPATIBLE");
  expect(badge).toHaveTextContent("Pipeline cannot run for this recording label space.");
  expect(screen.getByTestId("run-error-technical-details")).toHaveTextContent("Technical details");
  expect(badge).toHaveTextContent("Recording label space mismatch.");
});

test("known code keeps raw code + localized explanation + technical detail (zh-CN)", () => {
  renderWithLocale(
    <RunStatusBadge status="failed" errorType="INPUT_INCOMPATIBLE" errorMessage="Recording label space mismatch." />,
    "zh-CN",
  );
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("INPUT_INCOMPATIBLE");
  expect(badge).toHaveTextContent("当前输入与所选算法流水线不兼容。");
  expect(screen.getByTestId("run-error-technical-details")).toHaveTextContent("技术详情");
  expect(badge).toHaveTextContent("Recording label space mismatch.");
});

test("unknown code preserves the raw code and raw backend message without invented translation (en-US)", () => {
  renderWithLocale(
    <RunStatusBadge status="failed" errorType="MYSTERY_CODE_X" errorMessage="Something raw happened." />,
    "en-US",
  );
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("MYSTERY_CODE_X");
  expect(badge).toHaveTextContent("Something raw happened.");
  expect(screen.queryByTestId("run-error-technical-details")).toBeNull();
});

test("unknown code keeps raw message in zh-CN too", () => {
  renderWithLocale(
    <RunStatusBadge status="failed" errorType="MYSTERY_CODE_X" errorMessage="Something raw happened." />,
    "zh-CN",
  );
  const badge = screen.getByTestId("run-status-badge");
  expect(badge).toHaveTextContent("MYSTERY_CODE_X");
  expect(badge).toHaveTextContent("Something raw happened.");
  expect(screen.queryByText("技术详情")).toBeNull();
});

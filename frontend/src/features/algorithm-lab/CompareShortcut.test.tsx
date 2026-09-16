import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { CompareShortcut } from "./CompareShortcut";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import type { AnalysisRun } from "../../api/types";

function run(id: string, status: string, recordingId = "rec_1"): AnalysisRun {
  return {
    id,
    recordingId,
    pipelineId: "p",
    pipelineVersion: "1.0",
    executor: "local_cpu",
    status,
    parameters: {},
  } as AnalysisRun;
}

test("two completed runs with Ground Truth enable Compare and emit both ids", async () => {
  const user = userEvent.setup();
  const onCompare = vi.fn();
  render(
    renderWithLocalization(
      <CompareShortcut
        recordingId="rec_1"
        hasGroundTruth
        runs={[run("a", "completed"), run("b", "completed")]}
        onCompare={onCompare}
      />,
    ),
  );
  await user.click(screen.getByRole("checkbox", { name: "a" }));
  await user.click(screen.getByRole("checkbox", { name: "b" }));
  await user.click(screen.getByRole("button", { name: "Compare" }));
  expect(onCompare).toHaveBeenCalledWith("a", "b");
});

test("no Ground Truth disables Compare with a localized reason", () => {
  render(
    renderWithLocalization(
      <CompareShortcut
        recordingId="rec_1"
        hasGroundTruth={false}
        runs={[run("a", "completed"), run("b", "completed")]}
        onCompare={vi.fn()}
      />,
    ),
  );
  expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
  expect(screen.getByTestId("compare-gt-required")).toHaveTextContent(/Ground Truth/);
});

test("non-completed runs cannot be selected", () => {
  render(
    renderWithLocalization(
      <CompareShortcut
        recordingId="rec_1"
        hasGroundTruth
        runs={[run("a", "completed"), run("c", "running")]}
        onCompare={vi.fn()}
      />,
    ),
  );
  expect(screen.getByRole("checkbox", { name: "c" })).toBeDisabled();
});

test("a single selected run keeps Compare disabled", async () => {
  const user = userEvent.setup();
  render(
    renderWithLocalization(
      <CompareShortcut
        recordingId="rec_1"
        hasGroundTruth
        runs={[run("a", "completed"), run("b", "completed")]}
        onCompare={vi.fn()}
      />,
    ),
  );
  await user.click(screen.getByRole("checkbox", { name: "a" }));
  expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
});

test("a third selection is rejected", async () => {
  const user = userEvent.setup();
  render(
    renderWithLocalization(
      <CompareShortcut
        recordingId="rec_1"
        hasGroundTruth
        runs={[run("a", "completed"), run("b", "completed"), run("c", "completed")]}
        onCompare={vi.fn()}
      />,
    ),
  );
  await user.click(screen.getByRole("checkbox", { name: "a" }));
  await user.click(screen.getByRole("checkbox", { name: "b" }));
  await user.click(screen.getByRole("checkbox", { name: "c" }));
  expect(screen.getByRole("checkbox", { name: "c" })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Compare" })).not.toBeDisabled();
});

import type { MessageKey } from "../../localization/types";
import { experimentStatusKey, reasonKey, runStatusKey } from "./statusModel";

/**
 * L3: statusModel is SEMANTIC ONLY — it maps raw backend identity to
 * localization message keys. Language lives in the resources, not here.
 */
test("maps AnalysisRun statuses to semantic localization keys", () => {
  expect(runStatusKey("pending")).toBe<MessageKey | null>("status.pending");
  expect(runStatusKey("running")).toBe<MessageKey | null>("status.running");
  expect(runStatusKey("completed")).toBe<MessageKey | null>("status.completed");
  expect(runStatusKey("failed")).toBe<MessageKey | null>("status.failed");
  expect(runStatusKey("interrupted")).toBe<MessageKey | null>("status.interrupted");
  expect(runStatusKey("unknown_status")).toBeNull();
});

test("maps DatasetExperiment statuses to semantic localization keys", () => {
  expect(experimentStatusKey("pending")).toBe<MessageKey | null>("status.pending");
  expect(experimentStatusKey("running")).toBe<MessageKey | null>("status.running");
  expect(experimentStatusKey("evaluating")).toBe<MessageKey | null>("status.evaluating");
  expect(experimentStatusKey("completed")).toBe<MessageKey | null>("status.completed");
  expect(experimentStatusKey("completed_with_failures")).toBe<MessageKey | null>("status.completedWithFailures");
  expect(experimentStatusKey("failed")).toBe<MessageKey | null>("status.failed");
  expect(experimentStatusKey("weird")).toBeNull();
});

test("maps known bounded codes to semantic keys; unknown stays null", () => {
  expect(reasonKey("AUTO_NO_RUNNABLE_EXECUTOR")).toBe<MessageKey | null>("reason.AUTO_NO_RUNNABLE_EXECUTOR");
  expect(reasonKey("INPUT_INCOMPATIBLE")).toBe<MessageKey | null>("reason.INPUT_INCOMPATIBLE");
  expect(reasonKey("ANALYSIS_LAUNCH_AMBIGUOUS")).toBe<MessageKey | null>("reason.ANALYSIS_LAUNCH_AMBIGUOUS");
  expect(reasonKey("EXECUTION_NOT_CERTIFIED")).toBe<MessageKey | null>("reason.EXECUTION_NOT_CERTIFIED");
  expect(reasonKey("EXECUTION_CAPABILITY_UNAVAILABLE")).toBe<MessageKey | null>("reason.EXECUTION_CAPABILITY_UNAVAILABLE");
  expect(reasonKey("totally_unknown")).toBeNull();
  expect(reasonKey(null)).toBeNull();
  expect(reasonKey("")).toBeNull();
});

import { describeExperimentStatus, describeReasonCode, describeRunStatus } from "./statusModel";

test("maps AnalysisRun statuses to readable labels", () => {
  expect(describeRunStatus("pending")).toBe("Pending");
  expect(describeRunStatus("running")).toBe("Running");
  expect(describeRunStatus("completed")).toBe("Completed");
  expect(describeRunStatus("failed")).toBe("Failed");
  expect(describeRunStatus("interrupted")).toBe("Interrupted");
});

test("maps DatasetExperiment statuses to readable labels", () => {
  expect(describeExperimentStatus("pending")).toBe("Pending");
  expect(describeExperimentStatus("running")).toBe("Running");
  expect(describeExperimentStatus("evaluating")).toBe("Evaluating");
  expect(describeExperimentStatus("completed")).toBe("Completed");
  expect(describeExperimentStatus("completed_with_failures")).toBe("Completed with failures");
  expect(describeExperimentStatus("failed")).toBe("Failed");
});

test("describes the approved bounded reason classes", () => {
  expect(describeReasonCode("EXECUTION_CAPABILITY_UNAVAILABLE")).toBe("Executor is not available for this input.");
  expect(describeReasonCode("EXECUTION_NOT_CERTIFIED")).toBe("Executor is not certified for this release/runtime.");
  expect(describeReasonCode("INPUT_INCOMPATIBLE")).toBe("Pipeline cannot run for this recording label space.");
  expect(describeReasonCode("ANALYSIS_LAUNCH_AMBIGUOUS")).toBe("The run was interrupted with an ambiguous launch state.");
  expect(describeReasonCode("AUTO_NO_RUNNABLE_EXECUTOR")).toBe("No execution environment is runnable for this request.");
});

test("an unknown code is preserved verbatim (never normalized away)", () => {
  expect(describeReasonCode("SOME_UNKNOWN_CODE")).toBe("SOME_UNKNOWN_CODE");
  expect(describeReasonCode(null)).toBeNull();
  expect(describeReasonCode("")).toBeNull();
});

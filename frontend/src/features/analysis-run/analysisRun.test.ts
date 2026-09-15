import { buildAnalysisRunRequest } from "./requestBuilder";

test("manual mode builds a request with the exact executor and no auto mode", () => {
  const request = buildAnalysisRunRequest({
    recordingId: "rec_1",
    pipelineId: "dummy",
    environment: { mode: "manual", executor: "local_cpu" },
  });
  expect(request).toEqual({
    recordingId: "rec_1",
    pipelineId: "dummy",
    executor: "local_cpu",
    parameters: {},
  });
  expect("executionMode" in request).toBe(false);
});

test("manual mode works for every concrete executor", () => {
  for (const executor of ["local_cpu", "local_gpu", "remote_gpu"]) {
    const request = buildAnalysisRunRequest({
      recordingId: "rec_1",
      pipelineId: "dummy",
      environment: { mode: "manual", executor },
    });
    expect(request.executor).toBe(executor);
    expect("executionMode" in request).toBe(false);
  }
});

test("auto mode builds a request with executionMode auto and no executor", () => {
  const request = buildAnalysisRunRequest({
    recordingId: "rec_1",
    pipelineId: "dummy",
    environment: { mode: "auto", executor: null },
  });
  expect(request).toEqual({
    recordingId: "rec_1",
    pipelineId: "dummy",
    executionMode: "auto",
    parameters: {},
  });
  expect("executor" in request).toBe(false);
});

test("auto mode does NOT leak a resolved executor even if one is present in the value", () => {
  const request = buildAnalysisRunRequest({
    recordingId: "rec_1",
    pipelineId: "dummy",
    environment: { mode: "auto", executor: "local_gpu" },
  });
  expect(request.executionMode).toBe("auto");
  expect("executor" in request).toBe(false);
});

test("parameters are always an empty object in generic V1", () => {
  const manual = buildAnalysisRunRequest({
    recordingId: "rec_1",
    pipelineId: "dummy",
    environment: { mode: "manual", executor: "local_cpu" },
  });
  const auto = buildAnalysisRunRequest({
    recordingId: "rec_1",
    pipelineId: "dummy",
    environment: { mode: "auto", executor: null },
  });
  expect(manual.parameters).toEqual({});
  expect(auto.parameters).toEqual({});
});

test("modelReleaseId is passed through only when explicitly provided", () => {
  const without = buildAnalysisRunRequest({
    recordingId: "rec_1",
    pipelineId: "dummy",
    environment: { mode: "manual", executor: "local_cpu" },
  });
  const withRelease = buildAnalysisRunRequest({
    recordingId: "rec_1",
    pipelineId: "dummy",
    environment: { mode: "manual", executor: "local_cpu" },
    modelReleaseId: "golden",
  });
  expect("modelReleaseId" in without).toBe(false);
  expect(withRelease.modelReleaseId).toBe("golden");
});

test("a manual request without an executor is rejected", () => {
  expect(() =>
    buildAnalysisRunRequest({
      recordingId: "rec_1",
      pipelineId: "dummy",
      environment: { mode: "manual", executor: null },
    }),
  ).toThrow();
});

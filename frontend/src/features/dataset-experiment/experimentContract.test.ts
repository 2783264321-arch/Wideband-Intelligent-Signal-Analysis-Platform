import { toCreateRequest, type ExperimentFormValue } from "./types";

function value(overrides: Partial<ExperimentFormValue> = {}): ExperimentFormValue {
  return {
    name: "Exp",
    datasetName: "spacenet",
    datasetSplit: "test",
    datasetLabelSpace: "spacenet_14",
    pluginId: "dummy",
    pluginVersion: "1.0",
    environment: { mode: "auto", executor: null },
    maxConcurrency: 1,
    ...overrides,
  };
}

test("auto experiment request omits executor and evaluation protocol", () => {
  const request = toCreateRequest(value({ environment: { mode: "auto", executor: null } }));
  expect(request).toEqual({
    name: "Exp",
    datasetName: "spacenet",
    datasetSplit: "test",
    datasetLabelSpace: "spacenet_14",
    pluginId: "dummy",
    pluginVersion: "1.0",
    executionMode: "auto",
    parameters: {},
    maxConcurrency: 1,
  });
  expect("executor" in request).toBe(false);
  expect("evaluationProtocol" in request).toBe(false);
});

test("manual experiment request carries the exact executor", () => {
  const request = toCreateRequest(value({ environment: { mode: "manual", executor: "local_gpu" } }));
  expect(request.executor).toBe("local_gpu");
  expect(request.executionMode).toBe("manual");
  expect("evaluationProtocol" in request).toBe(false);
});

test("manual experiment request without an executor is rejected", () => {
  expect(() => toCreateRequest(value({ environment: { mode: "manual", executor: null } }))).toThrow();
});

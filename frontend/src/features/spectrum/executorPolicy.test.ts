import { describe, expect, it } from "vitest";
import {
  resolveExecutorForPipeline,
  type AvailabilityState,
  type ExecutorResolution,
} from "./executorPolicy";

type Pipeline = {
  cpuSupported: boolean;
  executorsSupported: string[];
  recommendedExecutor: string;
};

const remoteOnly: Pipeline = { cpuSupported: false, executorsSupported: ["remote_gpu"], recommendedExecutor: "remote_gpu" };
const localOnly: Pipeline = { cpuSupported: true, executorsSupported: ["local_cpu"], recommendedExecutor: "local_cpu" };
const dualRemote: Pipeline = { cpuSupported: true, executorsSupported: ["local_cpu", "remote_gpu"], recommendedExecutor: "remote_gpu" };
const dualLocal: Pipeline = { cpuSupported: true, executorsSupported: ["local_cpu", "remote_gpu"], recommendedExecutor: "local_cpu" };
const neither: Pipeline = { cpuSupported: false, executorsSupported: ["some_other"], recommendedExecutor: "some_other" };

function expectResolution(actual: ExecutorResolution, executor: "remote_gpu" | "local_cpu" | null, reason: string | null) {
  expect(actual.executor).toBe(executor);
  if (reason === null) {
    expect(actual.disabledReason).toBeNull();
  } else {
    expect(actual.disabledReason).toBeTruthy();
  }
}

describe("resolveExecutorForPipeline — remoteOnly", () => {
  it("available -> remote_gpu", () => {
    const avail: AvailabilityState = { state: "available" };
    expectResolution(resolveExecutorForPipeline(remoteOnly, avail), "remote_gpu", null);
  });
  it("loading -> DISABLED", () => {
    expectResolution(resolveExecutorForPipeline(remoteOnly, { state: "loading" }), null, "Checking remote GPU");
  });
  it("unavailable -> DISABLED with reason", () => {
    expectResolution(resolveExecutorForPipeline(remoteOnly, { state: "unavailable", reason: "no card" }), null, "no card");
  });
  it("error -> DISABLED", () => {
    expectResolution(resolveExecutorForPipeline(remoteOnly, { state: "error" }), null, "Unable to check");
  });
});

describe("resolveExecutorForPipeline — localOnly", () => {
  it("always local_cpu (no availability dependency)", () => {
    expectResolution(resolveExecutorForPipeline(localOnly, { state: "unavailable" }), "local_cpu", null);
  });
});

describe("resolveExecutorForPipeline — dual recommended-remote", () => {
  it("loading -> DISABLED", () => {
    expectResolution(resolveExecutorForPipeline(dualRemote, { state: "loading" }), null, "Checking remote GPU");
  });
  it("available -> remote_gpu", () => {
    expectResolution(resolveExecutorForPipeline(dualRemote, { state: "available" }), "remote_gpu", null);
  });
  it("unavailable -> fall back to local_cpu", () => {
    expectResolution(resolveExecutorForPipeline(dualRemote, { state: "unavailable", reason: "no card" }), "local_cpu", null);
  });
  it("error -> fall back to local_cpu", () => {
    expectResolution(resolveExecutorForPipeline(dualRemote, { state: "error" }), "local_cpu", null);
  });
});

describe("resolveExecutorForPipeline — dual recommended-local", () => {
  it("always local_cpu", () => {
    expectResolution(resolveExecutorForPipeline(dualLocal, { state: "available" }), "local_cpu", null);
    expectResolution(resolveExecutorForPipeline(dualLocal, { state: "unavailable" }), "local_cpu", null);
  });
});

describe("resolveExecutorForPipeline — neither usable", () => {
  it("DISABLED", () => {
    expectResolution(resolveExecutorForPipeline(neither, { state: "available" }), null, "No executor");
  });
});
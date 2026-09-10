import { describe, expect, it } from "vitest";
import {
  resolveExecutorForPipeline,
  type AvailabilityState,
  type ExecutorResolution,
} from "./executorPolicy";

type Pipeline = {
  executorsSupported: string[];
  recommendedExecutor: string | null;
};

const remoteOnly: Pipeline = { executorsSupported: ["remote_gpu"], recommendedExecutor: "remote_gpu" };
const localOnly: Pipeline = { executorsSupported: ["local_cpu"], recommendedExecutor: "local_cpu" };
const dualRemote: Pipeline = { executorsSupported: ["local_cpu", "remote_gpu"], recommendedExecutor: "remote_gpu" };
const dualLocal: Pipeline = { executorsSupported: ["local_cpu", "remote_gpu"], recommendedExecutor: "local_cpu" };
const neither: Pipeline = { executorsSupported: ["some_other"], recommendedExecutor: null };
const noProvider: Pipeline = { executorsSupported: [], recommendedExecutor: null };

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

describe("resolveExecutorForPipeline — deployment-qualified only", () => {
  it("empty executorsSupported -> DISABLED (legacy cpuSupported must not create local_cpu)", () => {
    expectResolution(resolveExecutorForPipeline(noProvider, { state: "available" }), null, "No executor");
    expectResolution(
      resolveExecutorForPipeline(noProvider, { state: "unavailable", reason: "no provider" }),
      null,
      "No executor",
    );
  });
  it("local_cpu absent from executorsSupported -> never offered locally", () => {
    const remoteOnlyPipeline: Pipeline = { executorsSupported: ["remote_gpu"], recommendedExecutor: "remote_gpu" };
    expectResolution(resolveExecutorForPipeline(remoteOnlyPipeline, { state: "unavailable" }), null, "unavailable");
  });
  it("dual with null recommendation -> local_cpu fallback", () => {
    const dualNoRec: Pipeline = { executorsSupported: ["local_cpu", "remote_gpu"], recommendedExecutor: null };
    expectResolution(resolveExecutorForPipeline(dualNoRec, { state: "available" }), "local_cpu", null);
  });
});
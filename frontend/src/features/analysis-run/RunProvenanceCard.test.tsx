import { render, screen } from "@testing-library/react";
import { RunProvenanceCard } from "./RunProvenanceCard";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import type { AnalysisRun } from "../../api/types";

function run(overrides: Partial<AnalysisRun> = {}): AnalysisRun {
  return {
    id: "run_1",
    recordingId: "rec_1",
    pipelineId: "dummy",
    pipelineVersion: "1.0",
    executor: "local_cpu",
    status: "completed",
    parameters: {},
    hardwareInfo: null,
    executionMetadata: null,
    startedAt: null,
    finishedAt: null,
    errorType: null,
    errorMessage: null,
    workerPid: null,
    createdAt: null,
    ...overrides,
  };
}

test("shows the resolved concrete executor", () => {
  render(renderWithLocalization(<RunProvenanceCard run={run({ executor: "remote_gpu" })} />));
  expect(screen.getByTestId("run-provenance-card")).toHaveTextContent("Executor: remote_gpu");
});

test("shows bounded auto provenance when the backend projects it", () => {
  render(renderWithLocalization(<RunProvenanceCard run={run({
    executor: "local_gpu",
    executionMetadata: {
      requested_execution_mode: "auto",
      auto_reason_code: "AUTO_LOCAL_GPU_PREFERRED",
      auto_reason: "Local GPU certified and available.",
      workload_class: "GPU_BENEFICIAL",
    },
  })} />));
  const card = screen.getByTestId("run-provenance-card");
  expect(card).toHaveTextContent("Execution mode: auto");
  expect(card).toHaveTextContent("AUTO_LOCAL_GPU_PREFERRED");
  expect(card).toHaveTextContent("Local GPU certified and available.");
  expect(card).toHaveTextContent("Workload: GPU_BENEFICIAL");
});

test("absent execution provenance does not fabricate mode or reasons", () => {
  render(renderWithLocalization(<RunProvenanceCard run={run({ executor: "local_cpu", executionMetadata: null })} />));
  const card = screen.getByTestId("run-provenance-card");
  expect(card).toHaveTextContent("Executor: local_cpu");
  expect(card).not.toHaveTextContent(/Mode:/);
  expect(card).not.toHaveTextContent(/manual/i);
  expect(card).not.toHaveTextContent(/AUTO_/);
});

test("never invents a ModelRelease", () => {
  render(renderWithLocalization(<RunProvenanceCard run={run({
    executionMetadata: { model_release_id: "golden", release: "golden" },
  })} />));
  const card = screen.getByTestId("run-provenance-card");
  expect(card).not.toHaveTextContent(/golden/i);
  expect(card).not.toHaveTextContent(/release/i);
});

test("does not render arbitrary private execution metadata", () => {
  render(renderWithLocalization(<RunProvenanceCard run={run({
    executor: "remote_gpu",
    executionMetadata: {
      remote_profile: "autodl_primary",
      coordinator_token: "coord_abc123",
      request_id: "id_abc",
      environment_ref: "/root/miniconda3/bin/python",
      asset_path: "/abs/path/model.pt",
      cgroup_memory: "1234",
    },
  })} />));
  const card = screen.getByTestId("run-provenance-card");
  expect(card).toHaveTextContent("Remote execution configuration: autodl_primary");
  expect(card).not.toHaveTextContent(/coord_abc123/);
  expect(card).not.toHaveTextContent(/id_abc/);
  expect(card).not.toHaveTextContent(/miniconda|environment_ref/);
  expect(card).not.toHaveTextContent(/model\.pt|asset_path/);
  expect(card).not.toHaveTextContent(/cgroup/);
});

test("renders allowlisted remote device provenance with short SHAs", () => {
  render(renderWithLocalization(<RunProvenanceCard run={run({
    executor: "remote_gpu",
    hardwareInfo: { device_type: "cuda", device_name: "NVIDIA GeForce RTX 5090" },
    executionMetadata: {
      remote_profile: "autodl_primary",
      required_remote_runtime_commit: "6f24f3796efa99ca0c0f1099462f450127f25737",
      payload_sha256: "20b8130acb8cf9b92f7c95d640b12448790e34c7c5e01d2ab1858875fac4e7b3",
      remote_started_at: "2026-09-09T15:28:41+00:00",
      remote_finished_at: "2026-09-09T15:28:54+00:00",
    },
  })} />));
  const card = screen.getByTestId("run-provenance-card");
  expect(card).toHaveTextContent("Device: NVIDIA GeForce RTX 5090 (cuda)");
  expect(card).toHaveTextContent("Runtime commit version: 6f24f379");
  expect(card).toHaveTextContent("Payload SHA: 20b8130a");
});

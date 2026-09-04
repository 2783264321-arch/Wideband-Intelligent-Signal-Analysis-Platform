import { spectrumPathForRun } from "./spectrumNavigation";

test("preserves the analysis run and selected detection when returning to Spectrum", () => {
  expect(spectrumPathForRun("rec_123", "run_456", "det_789")).toBe(
    "/spectrum/rec_123?run=run_456&selected=det_789",
  );
});

test("preserves the analysis run when opening all Signals in Spectrum", () => {
  expect(spectrumPathForRun("rec_123", "run_456")).toBe("/spectrum/rec_123?run=run_456");
});

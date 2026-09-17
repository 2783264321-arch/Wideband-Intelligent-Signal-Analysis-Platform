import { render, screen } from "@testing-library/react";
import { SignalLinePlot } from "./SignalLinePlot";
import { deriveValues } from "./TimeDomainView";
import { SpectrumView } from "./SpectrumView";
import { formatFrequency, pickFrequencyScale } from "./frequency";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

test("magnitude is derived from I/Q as sqrt(I^2 + Q^2)", () => {
  expect(deriveValues("magnitude", [3, 0, -5], [4, 2, 12])).toEqual([5, 2, 13]);
});

test("phase is derived from I/Q as atan2(Q, I)", () => {
  const [first] = deriveValues("phase", [0], [1]);
  expect(first).toBeCloseTo(Math.PI / 2, 10);
  const [second] = deriveValues("phase", [-1], [0]);
  expect(second).toBeCloseTo(Math.PI, 10);
});

test("SignalLinePlot renders one or two series as SVG polylines", () => {
  render(
    renderWithLocalization(
      <SignalLinePlot
        series={[
          { label: "I", color: "#1677ff", points: [{ x: 0, y: -1 }, { x: 1, y: 1 }] },
          { label: "Q", color: "#faad14", points: [{ x: 0, y: 0 }, { x: 1, y: 0.5 }] },
        ]}
      />,
    ),
  );
  expect(screen.getByTestId("line-series-I")).toBeInTheDocument();
  expect(screen.getByTestId("line-series-Q")).toBeInTheDocument();
});

test("frequency units scale to GHz/MHz/kHz/Hz", () => {
  expect(pickFrequencyScale(2.4e9).unit).toBe("GHz");
  expect(pickFrequencyScale(30e6).unit).toBe("MHz");
  expect(pickFrequencyScale(20e3).unit).toBe("kHz");
  expect(pickFrequencyScale(500).unit).toBe("Hz");
  expect(formatFrequency(2.441e9, pickFrequencyScale(2.441e9))).toBe("2.441000");
});

test("SpectrumView renders returned frequency/power data", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    frequency_hz: [2.4405e9, 2.441e9, 2.4415e9],
    power_db: [-60, -8, -70],
    fft_size: 4096,
    segment_count: 8,
  }), { status: 200 })));
  render(renderWithLocalization(<SpectrumView recordingId="rec_x" />));
  expect(await screen.findByTestId("spectrum-plot")).toBeInTheDocument();
  expect(screen.getByTestId("spectrum-summary")).toHaveTextContent("FFT 4096 · 8 segments");
  expect(screen.getByTestId("line-series-Power (dB)")).toBeInTheDocument();
  vi.unstubAllGlobals();
});

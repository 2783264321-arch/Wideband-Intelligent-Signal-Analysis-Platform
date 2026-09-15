import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SignalsPage } from "./SignalsPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const detectionsWire = [
  {
    id: "det_1",
    run_id: "run_42",
    recording_id: "rec_7",
    t_start_s: 0.25,
    t_end_s: 0.5,
    f_low_hz: 2_420_000_000,
    f_high_hz: 2_440_000_000,
    class_id: 2,
    class_name: "WiFi 20MHz 64QAM",
    confidence: 0.93,
  },
];

afterEach(() => { vi.unstubAllGlobals(); });

function renderPage(locale: "zh-CN" | "en-US") {
  return render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/signals/run_42"]}>
        <Routes>
          <Route path="/signals/:runId" element={<SignalsPage />} />
        </Routes>
      </MemoryRouter>,
      { locale },
    ),
  );
}

test("localizes the signals list shell in zh-CN while preserving raw identity and physical values", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(detectionsWire))));
  renderPage("zh-CN");

  expect(await screen.findByText("信号检测结果")).toBeInTheDocument();
  expect(screen.getByText("在频谱中查看")).toBeInTheDocument();
  expect(screen.getByText("信号类型")).toBeInTheDocument();
  expect(screen.queryByText("Signal Type")).toBeNull();

  // Raw technical identity and physical values remain unchanged.
  expect(screen.getByText("det_1")).toBeInTheDocument();
  expect(screen.getByText("WiFi 20MHz 64QAM")).toBeInTheDocument();
  expect(screen.getByText("93.0%")).toBeInTheDocument();
  expect(screen.getByText("2430.000 MHz")).toBeInTheDocument();
  expect(screen.getByText("20.000 MHz")).toBeInTheDocument();
  expect(screen.getByText("0.250000–0.500000 s")).toBeInTheDocument();
});

test("localizes the signals error shell in zh-CN and preserves the raw backend identity", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: { code: "BOOM", message: "transient" } }),
    { status: 503 },
  )));
  renderPage("zh-CN");

  expect(await screen.findByText("无法加载信号检测结果")).toBeInTheDocument();
  expect(screen.queryByText("Unable to load signals")).toBeNull();
  expect(screen.getByText(/BOOM: transient/)).toBeInTheDocument();
});

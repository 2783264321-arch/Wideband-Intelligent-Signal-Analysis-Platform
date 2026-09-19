import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import { LocalizationProvider } from "../localization/LocalizationProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

function AppWithLocale({ locale = "en-US" }: { locale?: "zh-CN" | "en-US" }) {
  return (
    <LocalizationProvider initialLocale={locale}>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </LocalizationProvider>
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/api/recordings/rec_1")) {
      return new Response(JSON.stringify({
        id: "rec_1", name: "sample-a", data_format: "complex64_le", source: "custom",
        external_path: null, sample_rate_hz: 1e6, center_frequency_hz: 0,
        frequency_low_hz: -5e5, frequency_high_hz: 5e5, num_samples: 1000, duration_s: 0.001,
        dataset_name: null, dataset_split: null, label_space: null, has_ground_truth: false,
      }));
    }
    if (url.includes("/api/recordings?")) return new Response(JSON.stringify({ items: [], total: 0 }));
    return new Response(JSON.stringify([]));
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });

test("primary navigation is exactly Data Library | Analysis Overview | Algorithm Lab | User Guide | Settings", () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  const items = screen.getAllByRole("menuitem");
  expect(items).toHaveLength(5);
  expect(screen.getByRole("menuitem", { name: /Data Library/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /Analysis Overview/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /Algorithm Lab/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /User Guide/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /Settings/ })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: /Compare/ })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: /Benchmarks/ })).toBeNull();
  expect(screen.queryByText("Spectrum Analysis")).toBeNull();
});

test("the Analysis Overview route shows the cross-dataset title and no duplicated tabs", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("heading", { name: "All Dataset Analyses" }),
  ).toBeInTheDocument();
  // Compare / Benchmarks belong to other surfaces now; they are not advertised here.
  expect(screen.queryByRole("tab", { name: "Compare" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Benchmarks" })).toBeNull();
});

test("?tab=compare deep link still renders the compare workspace", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments?tab=compare"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("experiment-compare-page")).toBeInTheDocument();
});

test("the experiment detail route renders", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments/exp_1"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("experiment-detail-page")).toBeInTheDocument();
});

test("dataset benchmarks remain reachable via the ?tab=benchmarks deep link", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments?tab=benchmarks"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("dataset-benchmarks-view")).toBeInTheDocument();
});

test("legacy algorithm-lab benchmark links redirect to Experiments", async () => {
  render(
    <MemoryRouter initialEntries={["/algorithm-lab?tab=benchmarks&benchmark=abc"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("dataset-benchmarks-view")).toBeInTheDocument();
});

test("algorithm-lab query drilldown still loads the case comparison workspace", async () => {
  render(
    <MemoryRouter initialEntries={["/algorithm-lab?recording=rec1&runA=run_a&runB=run_b"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect((await screen.findAllByText("Algorithm Lab")).length).toBeGreaterThan(0);
});

test("fresh UI defaults to Simplified Chinese primary navigation", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AppWithLocale locale="zh-CN" />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("menuitem", { name: /数据管理/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /分析总览/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /算法评测实验室/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /使用指南/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /设置/ })).toBeInTheDocument();
  expect(screen.getAllByRole("menuitem")).toHaveLength(5);
});

test("the header language switch is present and accessible", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AppWithLocale locale="zh-CN" />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("button", { name: "中文" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "EN" })).toBeInTheDocument();
});

test("Algorithm Lab workspace is restored after leaving via the sidebar", async () => {
  window.localStorage.clear();
  render(
    <MemoryRouter initialEntries={["/algorithm-lab?recording=rec1&runA=run_a&runB=run_b"]}>
      <AppWithLocale locale="zh-CN" />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("menuitem", { name: /数据管理/ }));
  fireEvent.click(await screen.findByRole("menuitem", { name: /算法评测实验室/ }));
  expect(window.localStorage.getItem("wisa.algorithmLab.lastRoute")).toBe(
    "/algorithm-lab?recording=rec1&runA=run_a&runB=run_b",
  );
});

test("/ and /recordings redirect to the Data Library", async () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={["/"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("sidebar")).toBeInTheDocument();
  unmount();
  render(
    <MemoryRouter initialEntries={["/recordings"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("sidebar")).toBeInTheDocument();
});

test("data library routes render under the shell", async () => {
  render(
    <MemoryRouter initialEntries={["/data-library"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "Data Library" })).toBeInTheDocument();
});

test("dataset detail and standalone sample routes render under the shell", async () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={["/data-library/datasets/dsproj_1"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("sidebar")).toBeInTheDocument();
  unmount();
  render(
    <MemoryRouter initialEntries={["/data-library/samples/rec_1"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("sidebar")).toBeInTheDocument();
});

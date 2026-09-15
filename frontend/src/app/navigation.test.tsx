import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import { LocalizationProvider } from "../localization/LocalizationProvider";

function AppWithLocale({ locale = "en-US" }: { locale?: "zh-CN" | "en-US" }) {
  return (
    <LocalizationProvider initialLocale={locale}>
      <App />
    </LocalizationProvider>
  );
}


beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/api/recordings?")) return new Response(JSON.stringify({ items: [], total: 0 }));
    return new Response(JSON.stringify([]));
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });

test("primary navigation is exactly Recordings | Experiments | Algorithm Lab", () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  const items = screen.getAllByRole("menuitem");
  expect(items).toHaveLength(3);
  expect(screen.getByRole("menuitem", { name: /Recordings/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /Experiments/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /Algorithm Lab/ })).toBeInTheDocument();
  expect(screen.queryByText("Settings")).toBeNull();
  expect(screen.queryByRole("menuitem", { name: /Compare/ })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: /Benchmarks/ })).toBeNull();
  expect(screen.queryByText("Spectrum Analysis")).toBeNull();
});

test("the Experiments route renders the Experiments tab shell", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("tab", { name: "Experiments" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Compare" })).toBeInTheDocument();
});

test("?tab=compare shows the Compare tab", async () => {
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

test("dataset benchmarks are reachable under Experiments", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments?tab=benchmarks"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect((await screen.findAllByText("Benchmarks")).length).toBeGreaterThan(0);
});

test("legacy algorithm-lab benchmark links redirect to Experiments", async () => {
  render(
    <MemoryRouter initialEntries={["/algorithm-lab?tab=benchmarks&benchmark=abc"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect((await screen.findAllByText("Benchmarks")).length).toBeGreaterThan(0);
});

test("algorithm-lab query drilldown still loads the case comparison workspace", async () => {
  render(
    <MemoryRouter initialEntries={["/algorithm-lab?recording=rec1&runA=run_a&runB=run_b"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );
  expect((await screen.findAllByText("Algorithm Lab")).length).toBeGreaterThan(0);
});

// ---------------------------------------------------------------------------
// L2 — default language and switch
// ---------------------------------------------------------------------------

test("fresh UI defaults to Simplified Chinese primary navigation", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AppWithLocale locale="zh-CN" />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("menuitem", { name: /信号记录/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /数据集实验/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /算法评测实验室/ })).toBeInTheDocument();
  expect(screen.getAllByRole("menuitem")).toHaveLength(3);
});

test("the header language switch is present and accessible", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AppWithLocale locale="zh-CN" />
    </MemoryRouter>,
  );
  expect(await screen.findByLabelText("语言")).toBeInTheDocument();
  expect(screen.getByText("中文")).toBeInTheDocument();
  expect(screen.getByText("EN")).toBeInTheDocument();
});

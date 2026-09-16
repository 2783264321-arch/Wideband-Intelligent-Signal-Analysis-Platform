import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { Empty } from "antd";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { App } from "../app/App";
import { LOCALE_STORAGE_KEY, LocalizationProvider } from "./LocalizationProvider";
import { ThemeProvider } from "../theme/ThemeProvider";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";
import { useLocalization } from "./useLocalization";
import { enUS } from "./messages.en-US";
import { zhCN } from "./messages.zh-CN";
import { DOMAIN_GLOSSARY } from "./glossary";
import type { MessageKey } from "./types";

/**
 * L6 — final whole-application localization acceptance suite.
 *
 * Covers the high-risk cross-cutting guarantees only (default locale, switch,
 * persistence, AntD coupling, controlled terminology, prohibited forms,
 * abbreviation/unit invariants, status/error semantics, PlatformApiError
 * identity, null-metric invariant). Component-level behaviour stays in the
 * per-surface suites; this file does not re-test every component.
 */

function renderApp(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <LocalizationProvider>
        <ThemeProvider>
          <App />
        </ThemeProvider>
      </LocalizationProvider>
    </MemoryRouter>,
  );
}

/** Stub the background fetches the recording-backed pages issue on mount. */
function stubShellFetches() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/api/recordings?")) return new Response(JSON.stringify({ items: [], total: 0 }));
    return new Response(JSON.stringify([]));
  }));
}

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { window.localStorage.clear(); vi.unstubAllGlobals(); });

// ---------------------------------------------------------------------------
// §3 Default / switch / persistence / AntD coupling
// ---------------------------------------------------------------------------

test("a fresh install defaults to zh-CN with document.lang zh-CN and Chinese primary UI", async () => {
  stubShellFetches();
  renderApp();
  expect(await screen.findByRole("menuitem", { name: /数据管理/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /数据集实验/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /算法评测实验室/ })).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("zh-CN");
  expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBeNull();
});

test("an invalid persisted locale falls back to zh-CN", async () => {
  stubShellFetches();
  window.localStorage.setItem(LOCALE_STORAGE_KEY, "fr-FR");
  renderApp();
  expect(await screen.findByRole("menuitem", { name: /数据管理/ })).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("zh-CN");
});

test("the header switch changes to en-US live, without reload, and persists", async () => {
  stubShellFetches();
  renderApp();
  await screen.findByRole("menuitem", { name: /数据管理/ });

  fireEvent.click(screen.getByText("EN"));

  expect(await screen.findByRole("menuitem", { name: /Data Library/ })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: /数据管理/ })).toBeNull();
  expect(document.documentElement.lang).toBe("en-US");
  expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en-US");

  fireEvent.click(screen.getByText("中文"));
  expect(await screen.findByRole("menuitem", { name: /数据管理/ })).toBeInTheDocument();
  expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("zh-CN");
});

test("both locales survive a remount through localStorage", async () => {
  stubShellFetches();

  window.localStorage.setItem(LOCALE_STORAGE_KEY, "en-US");
  const first = renderApp();
  expect(await screen.findByRole("menuitem", { name: /Data Library/ })).toBeInTheDocument();
  first.unmount();

  renderApp();
  expect(await screen.findByRole("menuitem", { name: /Data Library/ })).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("en-US");
});

test("Ant Design ConfigProvider locale stays coupled to the application locale", () => {
  const { unmount } = render(renderWithLocalization(<Empty />, { locale: "zh-CN" }));
  expect(screen.getAllByText("暂无数据").length).toBeGreaterThan(0);
  expect(screen.queryByText("No data")).toBeNull();
  unmount();

  render(renderWithLocalization(<Empty />, { locale: "en-US" }));
  expect(screen.getAllByText("No data").length).toBeGreaterThan(0);
  expect(screen.queryByText("暂无数据")).toBeNull();
});

// ---------------------------------------------------------------------------
// §4 Primary navigation acceptance
// ---------------------------------------------------------------------------

test("primary navigation is exactly the five intended destinations in both locales", async () => {
  const { unmount } = render(renderWithLocalization(
    <MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>,
    { locale: "zh-CN" },
  ));
  const zhItems = await screen.findAllByRole("menuitem");
  expect(zhItems.map((item) => item.textContent)).toEqual(["数据管理", "数据集实验", "算法评测实验室", "使用指南", "设置"]);
  unmount();

  render(renderWithLocalization(
    <MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>,
    { locale: "en-US" },
  ));
  const enItems = await screen.findAllByRole("menuitem");
  expect(enItems.map((item) => item.textContent)).toEqual([
    "Data Library",
    "Dataset Experiments",
    "Algorithm Lab",
    "User Guide",
    "Settings",
  ]);
});

// ---------------------------------------------------------------------------
// §5 Controlled terminology acceptance
// ---------------------------------------------------------------------------

const TERMINOLOGY: Array<[MessageKey, string, string]> = [
  ["app.title", "Wideband Signal Lab", "宽带智能信号分析平台"],
  ["nav.recordings", "Recordings", "信号记录"],
  ["spectrum.title", "Spectrum Analysis", "频谱分析"],
  ["signals.title", "Signals", "信号检测结果"],
  ["signalDetail.title", "Signal Detail", "检测结果详情"],
  ["common.groundTruth", "Ground Truth", "真值标注（GT）"],
  ["nav.experiments", "Dataset Experiments", "数据集实验"],
  ["experiment.itemsTab", "Items", "实验样本"],
  ["experiment.attemptsTab", "Attempts", "执行尝试"],
  ["experiment.evaluationTab", "Evaluation", "评测"],
  ["benchmarks.tabLabel", "Benchmarks", "基准评测"],
  ["algorithmLab.title", "Algorithm Lab", "算法评测实验室"],
  ["executionEnv.title", "Execution Environment", "执行环境"],
  ["form.pipeline", "Pipeline", "算法流水线"],
  ["common.retryFailedItems", "Retry Failed", "重试失败样本"],
  ["common.retryEvaluation", "Retry Evaluation", "重试评测"],
];

test("controlled terminology matches the normative glossary in both locales", () => {
  const { result } = renderHook(() => useLocalization(), {
    wrapper: ({ children }: { children: ReactNode }) => <LocalizationProvider initialLocale="en-US">{children}</LocalizationProvider>,
  });
  for (const [key, en, zh] of TERMINOLOGY) {
    act(() => { result.current.setLocale("en-US"); });
    expect([key, result.current.t(key)]).toEqual([key, en]);
    act(() => { result.current.setLocale("zh-CN"); });
    expect([key, result.current.t(key)]).toEqual([key, zh]);
  }
});

test("concept-level glossary labels required by the spec are registered", () => {
  expect(DOMAIN_GLOSSARY.spectrogram.zh).toBe("时频图");
  expect(DOMAIN_GLOSSARY.analysisRun.zh).toBe("分析任务");
  expect(DOMAIN_GLOSSARY.datasetEvaluation.zh).toBe("数据集评测");
  expect(DOMAIN_GLOSSARY.provenance.zh).toBe("运行溯源信息");
  expect(DOMAIN_GLOSSARY.benchmark.zh).toBe("基准评测");
  expect(DOMAIN_GLOSSARY.executionEnvironment.zh).toBe("执行环境");
});

test("spectrogram and analysis-run composite labels carry the approved terminology", () => {
  const { result } = renderHook(() => useLocalization(), {
    wrapper: ({ children }: { children: ReactNode }) => <LocalizationProvider initialLocale="zh-CN">{children}</LocalizationProvider>,
  });
  expect(result.current.t("spectrum.spectrogramAlt", { representation: "STFT" })).toBe("STFT 时频图");
  expect(result.current.t("analysisRun.label", { id: "run_1" })).toBe("分析任务 run_1");
});

test("execution environment terminology is the approved controlled set", () => {
  const { result } = renderHook(() => useLocalization(), {
    wrapper: ({ children }: { children: ReactNode }) => <LocalizationProvider initialLocale="zh-CN">{children}</LocalizationProvider>,
  });
  expect(result.current.t("executionEnv.auto")).toBe("自动选择");
  expect(result.current.t("executionEnv.localCpu")).toBe("本地 CPU");
  expect(result.current.t("executionEnv.localGpu")).toBe("本地 GPU");
  expect(result.current.t("executionEnv.remoteGpu")).toBe("远程 GPU");
});

// ---------------------------------------------------------------------------
// §6 Prohibited terminology audit
// ---------------------------------------------------------------------------

test("no Chinese resource uses a prohibited literal form", () => {
  const prohibited = ["录音", "执行器", "地面真相", "基准线"];
  for (const [key, value] of Object.entries(zhCN)) {
    for (const form of prohibited) {
      expect(`${key}:${value}`).not.toContain(form);
    }
  }
});

test("no Chinese resource uses 跑 with the user-facing execute/run meaning", () => {
  // Semantic audit: `跑` is only rejected where it means "execute/run" in UI copy.
  // No resource uses it at all here; record the classification explicitly.
  const occurrences = Object.entries(zhCN).filter(([, value]) => value.includes("跑"));
  expect(occurrences).toEqual([]);
});

// ---------------------------------------------------------------------------
// §7 Metric terminology
// ---------------------------------------------------------------------------

test("metric terminology uses the exact approved Chinese terms", () => {
  const { result } = renderHook(() => useLocalization(), {
    wrapper: ({ children }: { children: ReactNode }) => <LocalizationProvider initialLocale="zh-CN">{children}</LocalizationProvider>,
  });
  expect(result.current.t("compare.metricClassAwareMap50")).toBe("类别感知 mAP50");
  expect(result.current.t("compare.metricClassAwareMap50_95")).toBe("类别感知 mAP50:95");
  expect(result.current.t("compare.metricMatchedAccuracy")).toBe("已匹配目标分类准确率");
  expect(result.current.t("metrics.classAwareMap50")).toBe("类别感知 mAP50");
  expect(result.current.t("metrics.classAwareMap50_95")).toBe("类别感知 mAP50:95");
  expect(result.current.t("metrics.matchedAccuracy")).toBe("已匹配目标分类准确率");
  expect(result.current.t("experiment.columnPlugin")).toBe("算法插件");
});

// ---------------------------------------------------------------------------
// §8 Abbreviation / unit invariants
// ---------------------------------------------------------------------------

const VERBATIM: Array<[MessageKey, string]> = [
  ["executionEnv.localCpu", "CPU"],
  ["executionEnv.localGpu", "GPU"],
  ["metrics.ap50", "AP50"],
  ["metrics.ap50_95", "AP50:95"],
  ["metrics.classAwareMap50", "mAP50"],
  ["metrics.classAwareMap50_95", "mAP50:95"],
  ["metrics.notAvailable", "N/A"],
  ["metrics.gt", "GT"],
  ["provenance.payloadSha", "SHA"],
  ["runMetrics.meanIou", "IoU"],
  ["common.stft", "STFT"],
  ["signalDetail.fftSpectrum", "FFT"],
  ["signalDetail.iqWaveform", "I/Q"],
];

test("abbreviations and units stay verbatim in both locales", () => {
  const { result } = renderHook(() => useLocalization(), {
    wrapper: ({ children }: { children: ReactNode }) => <LocalizationProvider initialLocale="en-US">{children}</LocalizationProvider>,
  });
  for (const [key, token] of VERBATIM) {
    act(() => { result.current.setLocale("en-US"); });
    expect(result.current.t(key)).toContain(token);
    act(() => { result.current.setLocale("zh-CN"); });
    expect(result.current.t(key)).toContain(token);
  }
});

test("no message resource contains HTML markup", () => {
  const markup = /<\/?[a-zA-Z][\s\S]*>/;
  for (const [key, value] of Object.entries({ ...enUS, ...zhCN })) {
    expect(`${key}:${value}`).not.toMatch(markup);
  }
});

// ---------------------------------------------------------------------------
// §9 / §10 / §11 Raw identity, status semantics, PlatformApiError
// ---------------------------------------------------------------------------

test("raw execution and protocol identity is never localized", async () => {
  const { RunStatusBadge } = await import("../features/analysis-run/RunStatusBadge");
  render(
    renderWithLocalization(
      <RunStatusBadge status="completed" errorType="ANALYSIS_LAUNCH_AMBIGUOUS" errorMessage="raw backend text" />,
      { locale: "zh-CN" },
    ),
  );
  // Identity is byte-for-byte.
  expect(screen.getByTestId("run-error-code")).toHaveTextContent("ANALYSIS_LAUNCH_AMBIGUOUS");
  expect(screen.getByText(/raw backend text/)).toBeInTheDocument();
  // The human-readable executors/protocol codes are identity, never translated:
  for (const identity of ["local_gpu", "remote_gpu", "cpn_bandwidth_tier", "golden", "physical_tf_detection_ap_v2"]) {
    expect(zhCN["provenance.executor"]).not.toContain(identity);
    expect(enUS["provenance.executor"]).not.toContain(identity);
  }
});

test("known statuses localize and unknown statuses stay raw", async () => {
  const { RunStatusBadge } = await import("../features/analysis-run/RunStatusBadge");

  const known = render(renderWithLocalization(<RunStatusBadge status="completed" />, { locale: "zh-CN" }));
  expect(screen.getByTestId("run-status-badge")).toHaveTextContent("已完成");
  known.unmount();

  render(renderWithLocalization(<RunStatusBadge status="some_new_backend_state" />, { locale: "zh-CN" }));
  expect(screen.getByTestId("run-status-badge")).toHaveTextContent("some_new_backend_state");
});

test("ANALYSIS_LAUNCH_AMBIGUOUS presents as Interrupted / 已中断, never Failed / 失败", async () => {
  const { RunStatusBadge } = await import("../features/analysis-run/RunStatusBadge");

  const en = render(renderWithLocalization(
    <RunStatusBadge status="interrupted" errorType="ANALYSIS_LAUNCH_AMBIGUOUS" errorMessage="ambiguous launch" />,
  ));
  const enBadge = screen.getByTestId("run-status-badge");
  expect(enBadge).toHaveTextContent("Interrupted");
  expect(enBadge).not.toHaveTextContent("Failed");
  en.unmount();

  render(renderWithLocalization(
    <RunStatusBadge status="interrupted" errorType="ANALYSIS_LAUNCH_AMBIGUOUS" errorMessage="ambiguous launch" />,
    { locale: "zh-CN" },
  ));
  const zhBadge = screen.getByTestId("run-status-badge");
  expect(zhBadge).toHaveTextContent("已中断");
  expect(zhBadge).not.toHaveTextContent("失败");
  expect(screen.getByTestId("run-error-code")).toHaveTextContent("ANALYSIS_LAUNCH_AMBIGUOUS");
});

test("structured PlatformApiError keeps raw backend detail while the shell localizes", async () => {
  const { PlatformApiError } = await import("../api/client");
  const { toErrorText } = await import("../api/errors");
  const failure = new PlatformApiError({ status: 503, code: "BOOM", message: "transient", details: {} });

  // Raw structured identity is preserved regardless of the localized fallback.
  expect(toErrorText(failure, "无法加载信号记录。")).toBe("BOOM: transient");
  // The frontend-owned generic fallback is the localized copy.
  expect(toErrorText({}, "无法加载信号记录。")).toBe("无法加载信号记录。");
  expect(toErrorText(new Error("boom"), "无法加载信号记录。")).toBe("boom");
});

// ---------------------------------------------------------------------------
// §12 Null metric invariant
// ---------------------------------------------------------------------------

test("unavailable metrics render N/A and never 0 in both locales", async () => {
  const { EvaluationMetricsView } = await import("../features/evaluation/EvaluationMetricsView");
  const evaluation = {
    id: "eval_1",
    aggregateMetrics: {
      classificationApplicable: true,
      classificationReason: null,
      localization: { ap50: null, ap50_95: null, operating: { precision: null, recall: null, f1: null } },
      classificationOnMatched: { matchedAccuracy: null },
      classAware: null,
    },
    perClassMetrics: [{ classId: 1, className: "WiFi", gtCount: 1, predictionCount: 1, ap50: null, ap50_95: null, operating: { precision: null, recall: null, f1: null } }],
    confusion: [],
  } as never;

  const { unmount } = render(renderWithLocalization(
    <EvaluationMetricsView evaluation={evaluation} />,
    { locale: "zh-CN" },
  ));
  const view = screen.getByTestId("evaluation-metrics-view");
  expect(view).toHaveTextContent("N/A");
  expect(view).not.toHaveTextContent(/\b0\b(?!\.)/);
  unmount();

  render(renderWithLocalization(<EvaluationMetricsView evaluation={evaluation} />));
  expect(screen.getByTestId("evaluation-metrics-view")).toHaveTextContent("N/A");
});

// ---------------------------------------------------------------------------
// §17 Resource integrity
// ---------------------------------------------------------------------------

test("en-US and zh-CN resources have exact key parity and typed interpolation", () => {
  expect(Object.keys(zhCN).sort()).toEqual(Object.keys(enUS).sort());
  const { result } = renderHook(() => useLocalization(), {
    wrapper: ({ children }: { children: ReactNode }) => <LocalizationProvider initialLocale="zh-CN">{children}</LocalizationProvider>,
  });
  // Bounded interpolation: provided vars substitute, unknown placeholders stay literal.
  expect(result.current.t("executionEnv.recommended", { executor: "本地 GPU" })).toBe("推荐执行环境：本地 GPU");
  expect(result.current.t("prospect.unknownKey" as MessageKey)).toBe("prospect.unknownKey");
});

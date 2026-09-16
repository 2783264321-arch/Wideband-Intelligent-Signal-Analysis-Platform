import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { enUS } from "./messages.en-US";
import { zhCN } from "./messages.zh-CN";
import { LOCALE_STORAGE_KEY, LocalizationProvider } from "./LocalizationProvider";
import { useLocalization } from "./useLocalization";
import { DOMAIN_GLOSSARY } from "./glossary";

function wrapper({ children }: { children: ReactNode }) {
  return <LocalizationProvider>{children}</LocalizationProvider>;
}

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { window.localStorage.clear(); });

test("defaults to zh-CN when nothing is persisted", () => {
  const { result } = renderHook(() => useLocalization(), { wrapper });
  expect(result.current.locale).toBe("zh-CN");
  expect(result.current.t("nav.recordings")).toBe("信号记录");
});

test("restores persisted en-US", () => {
  window.localStorage.setItem(LOCALE_STORAGE_KEY, "en-US");
  const { result } = renderHook(() => useLocalization(), { wrapper });
  expect(result.current.locale).toBe("en-US");
  expect(result.current.t("nav.recordings")).toBe("Recordings");
  expect(document.documentElement.lang).toBe("en-US");
});

test("restores persisted zh-CN", () => {
  window.localStorage.setItem(LOCALE_STORAGE_KEY, "zh-CN");
  const { result } = renderHook(() => useLocalization(), { wrapper });
  expect(result.current.locale).toBe("zh-CN");
  expect(result.current.t("nav.experiments")).toBe("数据集实验");
});

test("invalid persisted locale falls back to zh-CN", () => {
  window.localStorage.setItem(LOCALE_STORAGE_KEY, "fr-FR");
  const { result } = renderHook(() => useLocalization(), { wrapper });
  expect(result.current.locale).toBe("zh-CN");
});

test("setLocale rerenders immediately and persists", () => {
  const { result } = renderHook(() => useLocalization(), { wrapper });
  act(() => { result.current.setLocale("en-US"); });
  expect(result.current.locale).toBe("en-US");
  expect(result.current.t("nav.experiments")).toBe("Dataset Experiments");
  expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en-US");
  act(() => { result.current.setLocale("zh-CN"); });
  expect(result.current.locale).toBe("zh-CN");
  expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("zh-CN");
});

test("message resources have exact key parity", () => {
  expect(Object.keys(zhCN).sort()).toEqual(Object.keys(enUS).sort());
});

test("interpolates bounded variables", () => {
  const { result } = renderHook(() => useLocalization(), { wrapper });
  expect(result.current.t("executionEnv.recommended", { executor: "本地 GPU" })).toContain("本地 GPU");
});

test("domain glossary matches the localized resources", () => {
  const { result } = renderHook(() => useLocalization(), { wrapper });
  expect(result.current.t("nav.recordings")).toBe(DOMAIN_GLOSSARY.recordings.zh);
  act(() => { result.current.setLocale("en-US"); });
  expect(result.current.t("nav.recordings")).toBe(DOMAIN_GLOSSARY.recordings.en);
});

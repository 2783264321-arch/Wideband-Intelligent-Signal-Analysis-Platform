# WISA V1.1 UX-A App Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the backend-shaped shell with a task-oriented shell: a
collapsible persisted sidebar, Data Library / Dataset Experiments / Algorithm
Lab / User Guide / Settings navigation, a system/light/dark theme preference, a
bilingual in-app User Guide, and restoration of the last meaningful Algorithm
Lab workspace.

**Architecture:** Keep the existing single `App.tsx` route table and
`MainLayout.tsx` shell. Add three new client-preference subsystems (theme,
sidebar collapse, workspace-route memory), two new pages (`Settings`, `Guide`),
and one Markdown renderer dependency. No backend changes. No change to
Algorithm Lab analytical semantics.

**Tech Stack:** React 18, TypeScript, Vite, react-router-dom v6, Ant Design 5
(`ConfigProvider`, `Layout`, `Menu`, `Segmented`, `Radio`), Vitest +
@testing-library/react. New dependency: `marked` (single package, zero runtime
dependencies).

**Spec:**
`docs/superpowers/specs/2026-09-16-v1-1-ux-productization-design.md`

## Global Constraints

```text
Sidebar is collapsible and the collapsed preference is persisted.
Sidebar destinations: Data Library, Dataset Experiments, Algorithm Lab,
    User Guide, Settings.
Theme supports system / light / dark and the preference is persisted.
Use Ant Design theme infrastructure (ConfigProvider theme algorithm) rather
    than two unrelated manual style systems.
Preserve bilingual zh-CN / en-US support; default locale is zh-CN.
Business state (recording/runA/runB) stays in the URL.
User preferences (locale, theme, sidebar collapsed, last workspace route) are
    persistent client preferences.
Temporary interaction state stays ephemeral.
The spectrum analysis page is NOT a first-level navigation item.
Do not duplicate the full WISA title in both sidebar and header.
User Guide is application documentation rendered from Markdown source.
Do not build a custom Markdown parser.
Do not change Algorithm Lab analytical semantics.
No backend changes. No Remote-GPU workflow. No GPU.
```

---

## File Map

Create:

```text
frontend/src/theme/types.ts
frontend/src/theme/themePreference.ts
frontend/src/theme/ThemeProvider.tsx
frontend/src/theme/useTheme.ts
frontend/src/theme/theme.test.tsx
frontend/src/app/useSidebarCollapsed.ts
frontend/src/app/sidebar.test.tsx
frontend/src/app/workspaceMemory.ts
frontend/src/app/workspaceMemory.test.ts
frontend/src/app/sectionTitle.ts
frontend/src/app/PageHeader.tsx
frontend/src/app/sectionTitle.test.ts
frontend/src/pages/SettingsPage.tsx
frontend/src/pages/SettingsPage.test.tsx
frontend/src/pages/UserGuidePage.tsx
frontend/src/pages/UserGuidePage.test.tsx
frontend/src/features/guide/guideContent.ts
frontend/src/features/guide/renderMarkdown.ts
frontend/src/features/guide/renderMarkdown.test.ts
frontend/src/features/guide/user-guide.zh-CN.md
frontend/src/features/guide/user-guide.en-US.md
frontend/src/features/guide/UserGuideContent.tsx
```

Modify:

```text
frontend/package.json                               (add "marked": "latest")
frontend/src/main.tsx                               (wrap ThemeProvider)
frontend/src/app/App.tsx                            (/guide, /settings routes)
frontend/src/app/MainLayout.tsx                     (5-item nav, collapse, header)
frontend/src/app/App.test.tsx                       (5 nav labels)
frontend/src/app/navigation.test.tsx                (5 nav items, workspace restore)
frontend/src/localization/messages.en-US.ts         (nav/theme/settings/guide/sidebar keys)
frontend/src/localization/messages.zh-CN.ts         (same keys)
frontend/src/localization/glossary.ts               (dataLibrary, userGuide)
frontend/src/localization/localization.acceptance.test.tsx (5 nav destinations)
frontend/src/pages/AlgorithmLabPage.tsx             (write workspace route memory)
```

---

## Interfaces

Consumes:

```text
useLocalization(): { locale: Locale; setLocale: (locale: Locale) => void; t: (key, vars?) => string }
LOCALE_STORAGE_KEY = "wisa.locale"
antd ConfigProvider locale (already mounted in LocalizationProvider)
react-router-dom: useLocation, useNavigate, useSearchParams
```

Produces (consumed by UX-B / UX-C and by this plan):

```ts
// theme/types.ts
export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

// theme/themePreference.ts
export const THEME_STORAGE_KEY = "wisa.theme";
export function readStoredThemePreference(): ThemePreference;
export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): ResolvedTheme;
export function subscribeSystemTheme(listener: (prefersDark: boolean) => void): () => void;

// theme/ThemeProvider.tsx
export interface ThemeContextValue {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
}
export function ThemeProvider({ children }: { children: React.ReactNode }): JSX.Element;

// theme/useTheme.ts
export function useTheme(): ThemeContextValue;

// app/useSidebarCollapsed.ts
export const SIDEBAR_STORAGE_KEY = "wisa.sidebarCollapsed";
export function useSidebarCollapsed(): {
  collapsed: boolean;
  setCollapsed: (value: boolean) => void;
  toggle: () => void;
};

// app/workspaceMemory.ts
export const ALGORITHM_LAB_ROUTE_KEY = "wisa.algorithmLab.lastRoute";
export function isMeaningfulAlgorithmLabSearch(search: string): boolean;
export function rememberAlgorithmLabRoute(search: string): void;
export function readAlgorithmLabRoute(): string | null;

// app/sectionTitle.ts
export function sectionTitleKey(pathname: string): MessageKey;

// app/PageHeader.tsx
export interface PageHeaderProps { titleKey: MessageKey; subtitleKey?: MessageKey; }
export function PageHeader({ titleKey, subtitleKey }: PageHeaderProps): JSX.Element;

// features/guide/guideContent.ts
export function guideMarkdownFor(locale: Locale): string;

// features/guide/renderMarkdown.ts
export function renderMarkdown(source: string): string;
```

---

## Task A1: Localization shell vocabulary

**Files:**
- Modify: `frontend/src/localization/messages.en-US.ts`
- Modify: `frontend/src/localization/messages.zh-CN.ts`
- Modify: `frontend/src/localization/glossary.ts`
- Modify: `frontend/src/localization/localization.acceptance.test.tsx`
- Test: `frontend/src/localization/localization.test.tsx`

**Interfaces:**
- Consumes: existing `messages.en-US.ts` / `messages.zh-CN.ts` key-parity contract.
- Produces: `nav.dataLibrary`, `nav.experiments` (value change), `nav.guide`,
  `nav.settings`, `theme.*`, `settings.*`, `guide.*`, `sidebar.*`.

- [ ] Add to `messages.en-US.ts` (adjacent to existing `nav.*`), and the exact
      matching keys to `messages.zh-CN.ts`:

```ts
"nav.dataLibrary": "Data Library",        // zh: "数据管理"
"nav.experiments": "Dataset Experiments", // zh: "数据集实验"  (value change)
"nav.guide": "User Guide",                // zh: "使用指南"
"nav.settings": "Settings",               // zh: "设置"
"theme.title": "Theme",                   // zh: "主题"
"theme.system": "System",                 // zh: "跟随系统"
"theme.light": "Light",                   // zh: "浅色"
"theme.dark": "Dark",                     // zh: "深色"
"settings.appearance": "Appearance",      // zh: "外观"
"settings.language": "Language",          // zh: "语言"
"settings.sidebar": "Sidebar",            // zh: "侧边栏"
"settings.sidebarCollapsed": "Collapsed by default", // zh: "默认收起"
"settings.openGuide": "Open User Guide",  // zh: "打开使用指南"
"guide.title": "User Guide",              // zh: "使用指南"
"guide.subtitle": "Task-oriented guides for the main WISA workflows.",
                                          // zh: "面向任务的 WISA 主要工作流程指南。"
"guide.open": "Guide",                    // zh: "指南"
"sidebar.collapse": "Collapse sidebar",   // zh: "收起侧边栏"
"sidebar.expand": "Expand sidebar",       // zh: "展开侧边栏"
```

- [ ] Remove the now-unused `nav.recordings` key from both files (the Data
      Library rename replaces it). Do not remove any other baseline key.
- [ ] Add glossary entries to `DOMAIN_GLOSSARY`:

```ts
dataLibrary: { en: "Data Library", zh: "数据管理" },
userGuide: { en: "User Guide", zh: "使用指南" },
```

- [ ] In `localization.acceptance.test.tsx` §4, update the "primary nav"
      assertion from 3 destinations to these 5, in both locales:

```text
en-US: Data Library | Dataset Experiments | Algorithm Lab | User Guide | Settings
zh-CN: 数据管理 | 数据集实验 | 算法评测实验室 | 使用指南 | 设置
```

- [ ] Add a test asserting the rename contract:

```ts
test("Recordings nav concept is replaced by Data Library", () => {
  const { getByText } = render(
    renderWithLocalization(<MainLayout />, { locale: "en-US" }) // wrapped in MemoryRouter
  );
  expect(getByText("Data Library")).toBeInTheDocument();
  expect(queryByText("Recordings")).toBeNull();
});
```

- [ ] Run focused: `npx vitest run src/localization`
- [ ] Expected: parity test passes (both files have identical key sets) and the
      updated nav acceptance test passes.
- [ ] Commit: `feat(ux-a): add shell localization vocabulary`

---

## Task A2: Theme preference provider

**Files:**
- Create: `frontend/src/theme/types.ts`, `theme/themePreference.ts`,
  `theme/ThemeProvider.tsx`, `theme/useTheme.ts`
- Test: `frontend/src/theme/theme.test.tsx`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `localStorage`, `window.matchMedia`, antd `ConfigProvider`.
- Produces: `useTheme()` and `ThemeProvider` (signatures above).

- [ ] Write the failing test `src/theme/theme.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./ThemeProvider";

function Probe() {
  const { preference, resolved } = useTheme();
  return <span data-testid="probe">{preference}:{resolved}</span>;
}

function setSystemPrefersDark(prefersDark: boolean) {
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
    matches: query.includes("dark") ? prefersDark : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
}

test("defaults to system and resolves light when the OS prefers light", () => {
  setSystemPrefersDark(false);
  render(<ThemeProvider><Probe /></ThemeProvider>);
  expect(screen.getByTestId("probe")).toHaveTextContent("system:light");
});

test("system preference resolves dark when the OS prefers dark", () => {
  setSystemPrefersDark(true);
  render(<ThemeProvider><Probe /></ThemeProvider>);
  expect(screen.getByTestId("probe")).toHaveTextContent("system:dark");
});

test("setPreference persists and sets documentElement data-theme", () => {
  setSystemPrefersDark(false);
  render(<ThemeProvider><button onClick={/* setPreference("dark") */}>{""}</button></ThemeProvider>);
  // assert localStorage["wisa.theme"] === "dark" and document.documentElement.dataset.theme === "dark"
});
```

- [ ] Run `npx vitest run src/theme` and observe the expected failure
      (`Cannot find module './ThemeProvider'`).
- [ ] Implement `theme/types.ts`:

```ts
export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";
```

- [ ] Implement `theme/themePreference.ts`:

```ts
import type { ResolvedTheme, ThemePreference } from "./types";
export const THEME_STORAGE_KEY = "wisa.theme";

export function readStoredThemePreference(): ThemePreference {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    return raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
  } catch {
    return "system";
  }
}

export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): ResolvedTheme {
  if (preference === "system") return systemPrefersDark ? "dark" : "light";
  return preference;
}

export function subscribeSystemTheme(listener: (prefersDark: boolean) => void): () => void {
  const mql = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = (event: MediaQueryListEvent) => listener(event.matches);
  mql.addEventListener("change", handler);
  return () => mql.removeEventListener("change", handler);
}
```

- [ ] Implement `theme/ThemeProvider.tsx` and `theme/useTheme.ts`. The provider
      resolves the theme, persists on change, mirrors it onto
      `document.documentElement.dataset.theme`, and renders a **nested**
      `ConfigProvider` (the outer one in `LocalizationProvider` keeps the Ant
      Design locale):

```tsx
import { ConfigProvider, theme as antdTheme } from "antd";
...
<ConfigProvider theme={{ algorithm: resolved === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm }}>
  {children}
</ConfigProvider>
```

- [ ] Modify `frontend/src/main.tsx` to wrap `App` in `ThemeProvider` inside
      `LocalizationProvider`.
- [ ] Run focused: `npx vitest run src/theme` and observe pass.
- [ ] Run `npx vitest run src/localization` (ConfigProvider nesting must not
      break locale tests).
- [ ] Commit: `feat(ux-a): add persisted system/light/dark theme provider`

---

## Task A3: Collapsible persisted sidebar

**Files:**
- Create: `frontend/src/app/useSidebarCollapsed.ts`, `frontend/src/app/sidebar.test.tsx`
- Modify: `frontend/src/app/MainLayout.tsx`

**Interfaces:**
- Consumes: `localStorage`, antd `Layout.Sider`.
- Produces: `useSidebarCollapsed()` (signature above); sidebar toggle button
  testid `sidebar-toggle`.

- [ ] Write the failing test asserting default expanded, toggle collapses and
      writes `wisa.sidebarCollapsed="true"`, and a remount restores collapsed:

```tsx
test("sidebar collapse persists across remount", () => {
  const { unmount } = render(<AppWithRouter initialEntries={["/recordings"]} />);
  fireEvent.click(screen.getByTestId("sidebar-toggle"));
  expect(localStorage.getItem("wisa.sidebarCollapsed")).toBe("true");
  unmount();
  render(<AppWithRouter initialEntries={["/recordings"]} />);
  expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "true");
});
```

- [ ] Run `npx vitest run src/app/sidebar.test.tsx`; observe failure.
- [ ] Implement `useSidebarCollapsed.ts` (default `false`; read/persist
      `"true"`/`"false"`; try/catch fallback).
- [ ] Update `MainLayout.tsx`:

```tsx
<Sider
  data-testid="sidebar"
  data-collapsed={collapsed ? "true" : "false"}
  collapsible
  collapsed={collapsed}
  trigger={null}
  width={220}
  collapsedWidth={64}
  theme="light"
  ...
/>
<Button
  data-testid="sidebar-toggle"
  type="text"
  aria-label={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
  icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
  onClick={toggle}
/>
```

- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-a): collapsible persisted sidebar`

---

## Task A4: Five-item navigation + section title

**Files:**
- Create: `frontend/src/app/sectionTitle.ts`, `frontend/src/app/sectionTitle.test.ts`
- Modify: `frontend/src/app/MainLayout.tsx`
- Test: `frontend/src/app/navigation.test.tsx`, `frontend/src/app/App.test.tsx`

**Interfaces:**
- Produces: `sectionTitleKey(pathname)`; nav menu keys
  `dataLibrary | experiments | algorithm | guide | settings`.

- [ ] Write failing test for `sectionTitleKey`:

```ts
expect(sectionTitleKey("/data-library")).toBe("nav.dataLibrary");
expect(sectionTitleKey("/data-library/datasets/dsproj_x")).toBe("nav.dataLibrary");
expect(sectionTitleKey("/experiments")).toBe("nav.experiments");
expect(sectionTitleKey("/algorithm-lab")).toBe("nav.algorithmLab");
expect(sectionTitleKey("/guide")).toBe("nav.guide");
expect(sectionTitleKey("/settings")).toBe("nav.settings");
expect(sectionTitleKey("/spectrum/rec_1")).toBe("spectrum.title");
```

- [ ] Implement `sectionTitle.ts` using `startsWith` checks; fall back to
      `app.title`.
- [ ] Update `MainLayout.tsx` to render exactly 5 `Menu` items and route them:

```ts
const paths: Record<string, string> = {
  dataLibrary: "/data-library",
  experiments: "/experiments",
  algorithm: "/algorithm-lab",
  guide: "/guide",
  settings: "/settings",
};
```

- [ ] Add a `useEffect`-free `useMemo` that maps the current `location.pathname`
      to the selected key using the same prefixes as `sectionTitleKey`.
- [ ] Update `navigation.test.tsx` and `App.test.tsx` to assert the 5 labels in
      default zh-CN and in en-US. Remove the "exactly 3 items" assertion.
- [ ] Run `npx vitest run src/app`; observe pass.
- [ ] Commit: `feat(ux-a): task-oriented five-item navigation`

---

## Task A5: Algorithm Lab workspace route restoration

**Files:**
- Create: `frontend/src/app/workspaceMemory.ts`, `frontend/src/app/workspaceMemory.test.ts`
- Modify: `frontend/src/app/MainLayout.tsx`
- Modify: `frontend/src/pages/AlgorithmLabPage.tsx`
- Test: `frontend/src/app/navigation.test.tsx`

**Interfaces:**
- Consumes: `useLocation`, `useSearchParams`, `useNavigate`.
- Produces: `rememberAlgorithmLabRoute`, `readAlgorithmLabRoute`,
  `isMeaningfulAlgorithmLabSearch`.

- [ ] Write failing unit tests:

```ts
test("only routes carrying a recording are meaningful", () => {
  expect(isMeaningfulAlgorithmLabSearch("?recording=rec_1&runA=a&runB=b")).toBe(true);
  expect(isMeaningfulAlgorithmLabSearch("?recording=rec_1")).toBe(true);
  expect(isMeaningfulAlgorithmLabSearch("")).toBe(false);
  expect(isMeaningfulAlgorithmLabSearch("?tab=case")).toBe(false);
});

test("remember then read round-trips the route", () => {
  rememberAlgorithmLabRoute("?recording=rec_1&runA=a&runB=b");
  expect(readAlgorithmLabRoute()).toBe("/algorithm-lab?recording=rec_1&runA=a&runB=b");
});
```

- [ ] Implement `workspaceMemory.ts` using `localStorage`, storing the full
      path with a leading `/algorithm-lab`; malformed stored values return null.
- [ ] In `AlgorithmLabPage.tsx`, add a `useEffect` that calls
      `rememberAlgorithmLabRoute(location.search)` when the search is
      meaningful. Do not change any analytical logic.
- [ ] In `MainLayout.tsx` algorithm nav click handler: if
      `location.pathname !== "/algorithm-lab"` or `location.search === ""`,
      navigate to `readAlgorithmLabRoute() ?? "/algorithm-lab"`; otherwise
      navigate to `/algorithm-lab`.
- [ ] Add the integration test to `navigation.test.tsx`:

```tsx
test("Algorithm Lab workspace is restored after leaving via the sidebar", () => {
  render(<AppWithLocation initialEntries={["/algorithm-lab?recording=rec_1&runA=run_a&runB=run_b"]} />);
  fireEvent.click(screen.getByText("数据管理"));
  fireEvent.click(screen.getByText("算法评测实验室"));
  expect(screen.getByTestId("location-probe").textContent)
    .toContain("recording=rec_1");
});
```

  where `AppWithLocation` renders a sibling `LocationProbe` that prints
  `useLocation().search`, inside the same `MemoryRouter`.
- [ ] Run `npx vitest run src/app`; observe pass.
- [ ] Commit: `feat(ux-a): restore last meaningful Algorithm Lab workspace`

---

## Task A6: Settings page

**Files:**
- Create: `frontend/src/pages/SettingsPage.tsx`, `frontend/src/pages/SettingsPage.test.tsx`
- Modify: `frontend/src/app/App.tsx` (route `/settings` → `SettingsPage`)

**Interfaces:**
- Consumes: `useTheme`, `useSidebarCollapsed`, `useLocalization`, `PageHeader`.
- Produces: `SettingsPage()`; testids `settings-theme`, `settings-language`,
  `settings-sidebar`, `settings-open-guide`.

- [ ] Write failing tests: selecting "Dark" calls `setPreference("dark")` and
      persists; the language radio switches locale; the sidebar switch toggles
      `wisa.sidebarCollapsed`; the guide link navigates to `/guide`.
- [ ] Implement `PageHeader.tsx` (Task A8 interface) and use it at the top of
      `SettingsPage`.
- [ ] Implement `SettingsPage` with antd `Segmented` for theme
      (`System | Light | Dark`), the existing `Radio.Group` language control,
      an antd `Switch` for sidebar collapsed default, and a `Link`/button to
      `/guide`.
- [ ] Remove the inline `SettingsPage` function from `App.tsx` and import the
      new page.
- [ ] Run `npx vitest run src/pages/SettingsPage.test.tsx`; observe pass.
- [ ] Commit: `feat(ux-a): settings page for user preferences`

---

## Task A7: User Guide route + Markdown rendering

**Files:**
- Create: `frontend/src/features/guide/renderMarkdown.ts`,
  `renderMarkdown.test.ts`, `guideContent.ts`, `UserGuideContent.tsx`,
  `user-guide.zh-CN.md`, `user-guide.en-US.md`
- Create: `frontend/src/pages/UserGuidePage.tsx`, `UserGuidePage.test.tsx`
- Modify: `frontend/src/app/App.tsx` (route `/guide`)
- Modify: `frontend/package.json` (via `npm install marked`)

**Interfaces:**
- Consumes: `useLocalization().locale`, Vite `?raw` Markdown imports.
- Produces: `renderMarkdown(source): string`, `guideMarkdownFor(locale): string`,
  `UserGuideContent()`, `UserGuidePage()`.

- [ ] Add the dependency: `npm install marked` from `frontend/` (writes
      `"marked": "latest"`, consistent with every other dependency).
- [ ] Write failing tests:

```ts
test("renders a heading and list from markdown", () => {
  const html = renderMarkdown("# Title\n\n- one\n- two");
  expect(html).toContain("<h1");
  expect(html).toContain("<li");
});

test("guide follows the app locale", () => {
  const { rerender } = render(renderWithLocalization(<UserGuideContent />, { locale: "zh-CN" }));
  expect(screen.getByText("分析单个 IQ 文件")).toBeInTheDocument();
  rerender(renderWithLocalization(<UserGuideContent />, { locale: "en-US" }));
  expect(screen.getByText("Analyze one IQ file")).toBeInTheDocument();
});
```

- [ ] Implement `renderMarkdown.ts`:

```ts
import { marked } from "marked";

export function renderMarkdown(source: string): string {
  const html = marked.parse(source, { async: false });
  return typeof html === "string" ? html : "";
}
```

  The rendered source is build-time bundled developer documentation, never
  user input. Raw HTML is therefore not sanitized at runtime; guide content is
  reviewed as source, like any other source file.
- [ ] Create `user-guide.zh-CN.md` and `user-guide.en-US.md`. Each must contain
      an H1 and the four workflow headings with the exact anchor strings used by
      tests:

```text
zh-CN H1: 使用指南
  ## 分析单个 IQ 文件
  ## 分析数据集
  ## 对比两条算法流水线
  ## 导入服务器生成的分析结果

en-US H1: User Guide
  ## Analyze one IQ file
  ## Analyze a dataset
  ## Compare two pipelines
  ## Import server-generated analysis results
```

- [ ] Implement `guideContent.ts`:

```ts
import zh from "./user-guide.zh-CN.md?raw";
import en from "./user-guide.en-US.md?raw";
import type { Locale } from "../../localization/types";

export function guideMarkdownFor(locale: Locale): string {
  return locale === "en-US" ? en : zh;
}
```

- [ ] Implement `UserGuideContent.tsx` as
      `<div className="wisa-guide" dangerouslySetInnerHTML={{ __html: renderMarkdown(guideMarkdownFor(locale)) }} />`.
- [ ] Implement `UserGuidePage.tsx` wrapping `UserGuideContent` with
      `PageHeader` (`guide.title` / `guide.subtitle`), and add the `/guide`
      route in `App.tsx`.
- [ ] Run `npx vitest run src/pages/UserGuidePage.test.tsx src/features/guide`; observe pass.
- [ ] Commit: `feat(ux-a): bilingual markdown user guide route`

---

## Task A8: Header consolidation

**Files:**
- Modify: `frontend/src/app/MainLayout.tsx`
- Test: `frontend/src/app/navigation.test.tsx`

**Interfaces:**
- Consumes: `sectionTitleKey`, `useTheme`, `useLocalization`, guide link.
- Produces: header testids `header-context`, `header-theme`, `header-language`,
  `header-guide`.

- [ ] Write failing test: on `/guide` the header context shows the guide title;
      the theme `Segmented` writes `wisa.theme`; the guide icon links to
      `/guide`.
- [ ] Update the header to contain:

```text
left   : t(sectionTitleKey(location.pathname))    (data-testid="header-context")
right  : Theme Segmented (System/Light/Dark)      (data-testid="header-theme")
         Language Radio.Group                      (data-testid="header-language")
         Guide icon link to /guide                 (data-testid="header-guide",
                                                    aria-label t("guide.open"))
```

- [ ] Remove the duplicate `{t("app.title")} · V1` header text (sidebar keeps
      the brand).
- [ ] Run `npx vitest run src/app`; observe pass.
- [ ] Commit: `feat(ux-a): consolidated context header with theme and guide controls`

---

## Task A9: Track boundary

- [ ] Run full frontend tests: `npm test -- --run` (from `frontend/`).
- [ ] Expected: all suites pass.
- [ ] Run production build: `npm run build`.
- [ ] Expected: `tsc -b` and `vite build` both succeed.
- [ ] Manual smoke (dev server): sidebar collapse persists on reload; theme
      system/light/dark applies instantly; guide switches language with locale;
      Algorithm Lab leave-and-return restores the comparison.
- [ ] Commit any remaining changes with `chore(ux-a): track boundary verification`.

---

## Self-Review Checklist

```text
[ ] No backend files changed.
[ ] No Algorithm Lab analytical semantics changed.
[ ] Spectrum page is not a nav item.
[ ] Exactly 5 primary nav destinations in both locales.
[ ] Sidebar collapse, theme, and workspace route persist in localStorage.
[ ] Guide renders from Markdown with no custom parser.
[ ] Existing locale parity and verbiage tests pass.
[ ] No Remote-GPU, no GPU, no sealed-baseline modification.
```

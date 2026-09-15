# Frontend Domain Localization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bilingual (zh-CN default / en-US) WISA frontend with professional signal-domain terminology, header switch `中文 | EN`, `localStorage` persistence, and AntD locales — no new dependencies, no backend changes, no route changes.

**Architecture:** A typed localization layer in `frontend/src/localization/` (`enUS` is the `MessageKey` source; `zhCN` is `Record<MessageKey, string>`), a `LocalizationProvider` (persistence + `documentElement.lang` + AntD `ConfigProvider`), and `useLocalization()` with bounded `{name}`/`{count}`/`{code}` interpolation. Components call `t(key)`; raw codes/IDs stay verbatim.

**Tech Stack:** React, TypeScript, Ant Design, localStorage, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-15-frontend-domain-localization-design.md`

## Global constraints

- Default `zh-CN`; alternative `en-US`; persistence key `wisa.locale`.
- Chinese is professional engineering terminology (approved controlled glossary), never literal translation. Prohibited: 录音, 执行器, 跑, 地面真相, 基准线.
- Never translate: raw codes/IDs/hashes/runtime refs; abbreviations `IQ RF SNR SIR FFT STFT LS-STFT IoU AP mAP CPU GPU CUDA GT`; units `Hz kHz MHz GHz s ms dB dBm AP50 AP50:95 mAP`; `N/A` (never `0`).
- Known bounded backend codes: localized explanation + raw code (+ raw message as technical detail). Unknown: preserve raw message, no invented translation.
- No new dependencies, no i18next; no backend changes; routes/query params unchanged; no F7/F8.

## Milestones

```text
L1 Localization foundation
L2 Global provider + language switch + persistence + AntD locale
L3 Domain glossary + statuses/errors
L4 Primary workflows migration
L5 Remaining pages/features migration
L6 Acceptance audit
```

### L1 — Localization foundation

**Files:** `localization/{types.ts,glossary.ts,messages.en-US.ts,messages.zh-CN.ts,LocalizationProvider.tsx,useLocalization.ts,localization.test.tsx}`

- [ ] RED: tests for default `zh-CN`, persisted `zh-CN`/`en-US`, invalid → `zh-CN`, `setLocale` rerenders + persists, resource parity, interpolation.
- [ ] GREEN: implement foundation; `MessageKey = keyof typeof enUS`; `zhCN: Record<MessageKey, string>`.
- [ ] Commit `feat(frontend): add typed localization foundation`.

### L2 — Global shell

**Files:** `main.tsx`, `app/MainLayout.tsx`

- [ ] RED: fresh UI shows `信号记录 / 数据集实验 / 算法评测实验室`; switching to EN shows `Recordings / Experiments / Algorithm Lab`; remount restores persisted locale; exactly three primary nav items.
- [ ] GREEN: wrap app in `LocalizationProvider`; header switch `中文 | EN` (accessible label, keyboard); primary nav labels via `t()`.
- [ ] Commit `feat(frontend): add language switch and persistence`.

### L3 — Domain glossary + statuses/errors

**Files:** `localization/*`, `features/analysis-run/statusModel.ts`, `RunStatusBadge.tsx`, `features/dataset-experiment/ExperimentProgressHeader.tsx`

- [ ] RED: `Recording → 信号记录`, `Ground Truth → 真值标注（GT）`, `Analysis Run → 分析任务`, `Execution Environment → 执行环境`, `Algorithm Lab → 算法评测实验室`, `Retry Failed Items → 重试失败样本`, `Retry Evaluation → 重试评测`; abbreviations/units unchanged; raw codes unchanged; known codes get localized explanation with raw code.
- [ ] GREEN: status/reason presentation resolves readable copy from localization while keeping raw identity.
- [ ] Commit `feat(frontend): localize signal-domain statuses and errors`.

### L4 — Primary workflows migration

**Files:** `pages/{RecordingsPage,SpectrumAnalysisPage,ExperimentsPage,ExperimentDetailPage,AlgorithmLabPage}.tsx`, `features/{execution-environment,analysis-run,dataset-experiment}/*`, `features/imports/ImportRunModal.tsx`

- [ ] Migrate user-visible static copy (headings, buttons, table headers, empty/loading states, explanatory Alerts) to `t()`; remove the English→Chinese literal mapping from components.
- [ ] RED/GREEN per surface; commit `feat(frontend): localize primary analysis workflows`.

### L5 — Remaining pages/features migration

**Files:** `features/{signals,signal-detail,spectrum,algorithm-lab,dataset-benchmarks,evaluation}/*`, `pages/SignalsPage.tsx`, `pages/SignalDetailPage.tsx`, `pages/ExperimentComparePage.tsx`

- [ ] Migrate remaining copy; commit `feat(frontend): localize experiments and algorithm lab`.

### L6 — Acceptance audit

- [ ] Add `test(frontend): add localization acceptance coverage`; full gate; source audit (no scattered literal pairs, no machine translation, no new deps).

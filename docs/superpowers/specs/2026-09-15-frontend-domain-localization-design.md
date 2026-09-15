# Frontend Domain Localization — Design Specification

Status: approved (product owner) — implementation follows this document.
Branch: `feature/frontend-v1`. No backend changes. No new dependencies.

## Goal

Make the whole WISA frontend bilingual with:

- default language `zh-CN` (Simplified Chinese)
- alternative `en-US`
- global switch `中文 | EN` in the header (keyboard accessible, no reload)
- persisted locally (`localStorage` key `wisa.locale`)
- Ant Design component locale via `ConfigProvider`

The Chinese UI is written as **professional engineering terminology** for wireless
communications / RF / wideband signal analysis / time-frequency analysis / signal
detection & classification / dataset evaluation / execution environments. It is
NOT a literal translation of the English strings.

## Architecture

```text
frontend/src/localization/
├── types.ts                  Locale, MessageKey, interpolation types
├── glossary.ts               normative domain concept → en/zh terminology
├── messages.en-US.ts         enUS messages (source of MessageKey)
├── messages.zh-CN.ts         zhCN: Record<MessageKey, string>
├── LocalizationProvider.tsx  context + persistence + documentElement.lang + AntD locale
├── useLocalization.ts        useLocalization() + t() with bounded interpolation
└── localization.test.tsx     foundation + acceptance tests
```

- `MessageKey = keyof typeof enUS`; `zhCN` is typed `Record<MessageKey, string>`
  so missing keys are compile errors (parity enforced by TypeScript + a runtime test).
- Bounded interpolation only: `{name}`, `{count}`, `{code}`. No HTML in resources.
- Provider API: `{ locale, setLocale, t }`.
- Persistence: missing/invalid → `zh-CN`; `en-US` → English; `zh-CN` → Chinese.
  `document.documentElement.lang` kept in sync.
- Ant Design `ConfigProvider` locale: `zh_CN` / `en_US` (no new packages).

## Terminology authority

`glossary.ts` (and the messages) implement the approved controlled glossary. Key
normative decisions:

| English | Chinese |
|---|---|
| Wideband Signal Lab | 宽带智能信号分析平台 |
| Recordings | 信号记录 |
| Spectrum Workbench / Spectrum Analysis | 频谱分析工作台 / 频谱分析 |
| Spectrogram | 时频图 |
| Signals | 信号检测结果 |
| Signal Detail | 检测结果详情 |
| Ground Truth | 真值标注（GT） |
| Analysis Run | 分析任务 |
| Run A / Run B | 任务 A / 任务 B |
| Dataset Experiment | 数据集实验 |
| Experiment Item / Attempt | 实验样本 / 执行尝试 |
| Evaluation / Dataset Evaluation | 评测 / 数据集评测 |
| Benchmark | 基准评测 |
| Compare | 对比 |
| Algorithm Lab | 算法评测实验室 |
| Case Analysis / Case Comparison | 单记录分析 / 信号实例对比 |
| Execution Environment | 执行环境 |
| Pipeline | 算法流水线 |
| Provenance | 运行溯源信息 |
| Retry Failed Items / Retry Evaluation | 重试失败样本 / 重试评测 |
| Per-class Metrics / Confusion | 分类别指标 / 混淆统计 |
| Shared Recording | 共有信号记录 |
| Auto / Local CPU / Local GPU / Remote GPU | 自动选择 / 本地 CPU / 本地 GPU / 远程 GPU |
| Unsupported / Not configured / Not certified / Temporarily unavailable | 不支持 / 未配置 / 未通过认证 / 暂时不可用 |

Prohibited literal forms: 录音, 执行器, 跑, 地面真相, 基准线.

## Abbreviations and units (never translated)

`IQ RF SNR SIR FFT STFT LS-STFT IoU AP mAP CPU GPU CUDA GT`; units `Hz kHz MHz GHz
s ms dB dBm AP50 AP50:95 mAP`; `N/A` for null metrics (never `0`).

## Raw technical identity (never translated)

Backend error/reason codes; pipeline/plugin IDs; plugin versions; model release
IDs; analysis run / experiment / evaluation / recording IDs; executor IDs; hashes
and SHA values; runtime refs — all byte-for-byte unchanged. Human-readable labels
may be localized; identity never is.

## Backend error/reason localization

- Known bounded codes: localized explanation + raw code (+ raw backend message as
  technical detail where useful).
- `ANALYSIS_LAUNCH_AMBIGUOUS` stays Interrupted / 已中断 — never Failed.
- Unknown codes/messages: preserve raw backend message; never machine-translate.
- `PlatformApiError` behavior unchanged.

## Status model

Centralize semantic identity: raw status/code → localization message key → `t(key)`.
Unknown codes fall back to the raw code. No backend semantics move into pages.

## Non-goals / invariants

- No backend changes, no new dependencies, no route/query changes (`?run=`,
  `?selected=`, `?recording=`, `?runA=`, `?runB=`, `?benchmark=` unchanged).
- F0–F6 invariants unchanged (backend execution authority, Auto is a mode, no
  frontend fallback, no plugin-id policy, dataset scope never calls
  recording-scoped `executor-availability`, no hardcoded ModelRelease, no null→0,
  comparison/polling identity protection, structured `PlatformApiError`).
- No F7/F8.

## Testing

- Foundation: defaults, persistence, invalid value, immediate rerender,
  resource parity, interpolation.
- Shell: default Chinese primary nav; switch to English; persistence on remount.
- Domain: high-risk terminology mapping; raw identity unchanged; abbreviations
  and units unchanged; unknown codes preserved.

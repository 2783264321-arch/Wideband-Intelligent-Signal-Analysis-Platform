# WISA V1 Batch Import UI Implementation Plan

- **Status:** Planned (no code executed during planning)
- **Design authority:** `docs/superpowers/specs/2026-09-16-v1-research-server-artifact-workflow-design.md`
- **Authoritative baseline:** `integration/v1-candidate` @ `6716eaef217de97c5746d0468d62abede9b1c63c`
- **Backend endpoint (already exists):** `POST /api/imported-runs/batch` → `BatchImportSummary`

## Goal

Expose the existing production batch-import endpoint through a bounded Windows WISA frontend flow, without adding SSH credentials or server job controls.

## Reuse-First Lock

| Seam | Location | Use |
|---|---|---|
| Single import UI | `frontend/src/features/imports/ImportRunModal.tsx` | **preserve unchanged**; batch gets a separate modal |
| Import trigger host | `frontend/src/pages/RecordingsPage.tsx` | render both modals |
| API client | `frontend/src/api/client.ts` (`apiUrl`, `structuredErrorFromResponse`, `PlatformApiError`) | add `importBatchRun` |
| Error text | `frontend/src/api/errors.ts` (`toErrorText`) | display structured errors |
| Types | `frontend/src/api/types.ts` | add `BatchImportSummary`, `BatchRunMapping` |
| Localization | `frontend/src/localization/messages.en-US.ts` (source of truth), `messages.zh-CN.ts`, `types.ts` (`MessageKey = keyof typeof enUS`) | add `batchImport.*` keys in both locales |

Bounded UI surface (explicitly allowed):

- select a Batch Analysis Package ZIP;
- upload/import;
- display `batch_id`, `item_count`, `detection_count`, `created_runs`/`existing_runs`, `matched_recordings`, `already_imported`, and the Recording → `AnalysisRun` mapping;
- surface structured validation failures.

Explicitly forbidden in the frontend: SSH keys/hosts, `RemoteProfile`, server job controls, `remote_gpu` toggles.

## Proposed Files

Production (modified):
- `frontend/src/api/types.ts` — add `BatchRunMapping`, `BatchImportSummary`.
- `frontend/src/api/client.ts` — add `importBatchRun(file: File): Promise<BatchImportSummary>`.
- `frontend/src/features/imports/BatchImportModal.tsx` — new modal (separate from `ImportRunModal`).
- `frontend/src/pages/RecordingsPage.tsx` — add trigger button + render `<BatchImportModal />`.
- `frontend/src/localization/messages.en-US.ts` — add `batchImport.*` keys.
- `frontend/src/localization/messages.zh-CN.ts` — add matching `batchImport.*` keys.

Tests (new/modified):
- `frontend/src/api/client.test.ts` — add batch-import client tests.
- `frontend/src/features/imports/BatchImportModal.test.tsx` — new.
- `frontend/src/features/imports/ImportRunModal.test.tsx` — unchanged regression.

## Exact Interfaces

`frontend/src/api/types.ts`:

```ts
export interface BatchRunMapping {
  recordingId: string;
  recordingName: string;
  analysisRunId: string;
}

export interface BatchImportSummary {
  batchId: string;
  importFingerprint: string;
  archiveSha256: string;
  datasetName: string;
  datasetSplit: string;
  pipelineId: string;
  pipelineVersion: string;
  labelSpace: string;
  itemCount: number;
  detectionCount: number;
  alreadyImported: boolean;
  createdRuns: number;
  existingRuns: number;
  createdDetections: number;
  matchedRecordings: number;
  missingRecordings: number;
  ambiguousRecordings: number;
  fingerprintMismatches: number;
  recordingRunMapping: BatchRunMapping[];
}
```

`frontend/src/api/client.ts`:

```ts
export async function importBatchRun(file: File): Promise<import("./types").BatchImportSummary> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(apiUrl("/api/imported-runs/batch"), { method: "POST", body });
  if (!response.ok) throw await structuredErrorFromResponse(response);
  return mapBatchImportSummary(await response.json() as BatchImportSummaryWire);
}
```

`mapBatchImportSummary` maps the snake_case wire to the camelCase domain type (no other reshaping).

## Localization Keys (exact)

Add to `messages.en-US.ts` and `messages.zh-CN.ts`:

```text
batchImport.action            "Import Batch" / "批量导入"
batchImport.title             "Import Batch Analysis Package" / "导入批量分析包"
batchImport.zipLabel          "Batch Analysis Package ZIP" / "批量分析包 ZIP"
batchImport.chooseFile        "Choose a Batch Analysis Package ZIP" / "请选择批量分析包 ZIP"
batchImport.zipHint           "Import a ZIP containing batch_manifest.json and per-item packages generated on a research/GPU server." / "导入包含 batch_manifest.json 及各样本子包的研究/GPU 服务器生成的 ZIP。"
batchImport.submit            "Import Batch" / "导入批量包"
batchImport.success           "Batch imported" / "批量包导入成功"
batchImport.idempotent        "Already imported; no new runs were created." / "该批量包已导入，未创建新的分析任务。"
batchImport.failure           "Unable to import batch package." / "无法导入批量分析包。"
batchImport.summaryTitle      "Import Summary" / "导入结果"
batchImport.batchId           "Batch ID" / "批量包 ID"
batchImport.dataset           "Dataset / Split" / "数据集 / 划分"
batchImport.pipeline          "Pipeline / Version" / "管线 / 版本"
batchImport.results           "Results" / "结果"
batchImport.itemCount         "Items" / "样本数"
batchImport.detectionCount    "Detections" / "检测数"
batchImport.createdRuns       "Created runs" / "新建分析任务"
batchImport.existingRuns      "Existing runs" / "已存在分析任务"
batchImport.matchedRecordings "Matched recordings" / "匹配的信号记录"
batchImport.mappingTitle      "Recording → AnalysisRun" / "信号记录 → 分析任务"
batchImport.columnRecording   "Recording" / "信号记录"
batchImport.columnRun         "AnalysisRun" / "分析任务"
batchImport.close             "Close" / "关闭"
```

`MessageKey` is derived from `en-US`; `zh-CN` must cover every added key.

---

## Task 1 — RED: client multipart contract

- [ ] Add to `frontend/src/api/client.test.ts`:
  - `test("importBatchRun posts multipart file to /api/imported-runs/batch")` — intercept `fetch`, assert URL `/api/imported-runs/batch`, method `POST`, `body instanceof FormData`, `body.get("file") === file`.
  - `test("importBatchRun maps snake_case summary to camelCase")` — respond with a full `BatchImportSummaryWire`; assert every camelCase field and one `recordingRunMapping` entry.
  - `test("importBatchRun preserves the structured backend error")` — respond `422` with `{ error: { code: "BATCH_RECORDING_NOT_FOUND", message: "...", details: {} } }`; assert thrown `PlatformApiError.code === "BATCH_RECORDING_NOT_FOUND"` and `display` contains the message.
- [ ] Run RED:

```bash
(cd frontend && npm test -- --run src/api/client.test.ts)
```

- [ ] Expect failure: `importBatchRun` is not exported.

## Task 2 — Implement client + types

- [ ] Add `BatchImportSummaryWire` + `mapBatchImportSummary` and `importBatchRun` in `client.ts`.
- [ ] Add `BatchRunMapping`, `BatchImportSummary` in `types.ts`.
- [ ] Run Task 1 tests → GREEN.

## Task 3 — RED: modal behavior

- [ ] Add `frontend/src/features/imports/BatchImportModal.test.tsx` with:
  1. `renders zod-free file input and disables submit until a file is chosen` — assert the submit button is disabled with no file, enabled after `fireEvent.change` with a `File`.
  2. `shows new-import summary fields and mapping on success` — mock `importBatchRun` resolving `{ batchId, datasetName, datasetSplit, pipelineId, pipelineVersion, itemCount, detectionCount, alreadyImported: false, createdRuns: 2, existingRuns: 0, matchedRecordings: 2, recordingRunMapping: [...] }`; assert batch id, dataset/split, pipeline/version, counts, and both mapping rows render.
  3. `shows idempotent state on re-import` — resolve `{ alreadyImported: true, createdRuns: 0, existingRuns: 2, ... }`; assert the idempotent message renders and `created runs = 0`.
  4. `renders structured validation failure via toErrorText` — mock rejection with `new PlatformApiError({ status: 409, code: "BATCH_IMPORT_STATE_INCONSISTENT", message: "Partial or conflicting prior semantic import state exists.", details: {} })`; assert the alert contains `BATCH_IMPORT_STATE_INCONSISTENT`.
  5. `resets state when closed and reopened` — import success, close, reopen; assert the file input and summary are cleared.
  6. `defaults to zh-CN localized labels` — render within `LocalizationProvider initialLocale="zh-CN"`; assert the Chinese submit label and summary title.
- [ ] Run RED:

```bash
(cd frontend && npm test -- --run src/features/imports/BatchImportModal.test.tsx)
```

- [ ] Expect failure: module `BatchImportModal` does not exist.

## Task 4 — Implement `BatchImportModal`

- [ ] Create `BatchImportModal.tsx` with props `{ open: boolean; onClose: () => void }`.
- [ ] State: `file`, `submitting`, `error`, `summary`.
- [ ] `canSubmit = !submitting && file !== null && summary === null`.
- [ ] On submit: `importBatchRun(file)`; catch with `toErrorText(reason, t("batchImport.failure"))`.
- [ ] Render summary fields and the mapping list; render the idempotent message when `summary.alreadyImported`.
- [ ] `resetAndClose()` clears `file`, `error`, `summary`, then calls `onClose()`.
- [ ] No credentials/job controls anywhere in the component.
- [ ] Run Task 3 tests → GREEN.

## Task 5 — Wire trigger + localization

- [ ] Add `batchImportOpen` state and a trigger button using `t("batchImport.action")` in `RecordingsPage.tsx` (do not alter the existing Import Run button).
- [ ] Render `<BatchImportModal open={batchImportOpen} onClose={() => setBatchImportOpen(false)} />`.
- [ ] Add the exact localization keys from the table above to both locale files.
- [ ] Run localization tests:

```bash
(cd frontend && npm test -- --run src/localization/localization.test.tsx src/localization/localization.acceptance.test.tsx)
```

- [ ] Require `0 failed`.

## Task 6 — Focused frontend regression + build

```bash
(cd frontend && npm test -- --run src/features/imports src/api/client.test.ts src/localization)
(cd frontend && npm run build)
```

- [ ] Require `0 failed`; require `tsc -b && vite build` to succeed (typed `MessageKey` coverage proves zh-CN completeness).
- [ ] Confirm `ImportRunModal.test.tsx` still passes unchanged.

## Task 7 — Commit

- [ ] Commit A: `feat(frontend): add batch analysis package import`.
- [ ] Commit B (if tests were split): `test(frontend): cover batch import UI`.
- [ ] No backend/scripts changes; no SSH or server credentials introduced.
- [ ] `git diff --check` clean.

## Cross-Plan Contract (must match Plans 1 and 3)

- Consumes the existing `POST /api/imported-runs/batch` endpoint; no new endpoint.
- Summary fields correspond exactly to backend `BatchImportSummary` (BAPv1).
- Inconsistent prior state is displayed from `BATCH_IMPORT_STATE_INCONSISTENT`; the UI never retries automatically.
- No `platform.db` sync, no `remote_gpu`, no GPU dependence.

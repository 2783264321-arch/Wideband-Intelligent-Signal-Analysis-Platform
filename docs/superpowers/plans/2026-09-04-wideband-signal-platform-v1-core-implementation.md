# Wideband Signal Platform V1 Core Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver milestones M0–M4 as a runnable local-first vertical slice: five-page Web shell, Recording import, real STFT spectrum viewing, physical-coordinate detection/GT overlays, Signal views, and a subprocess-backed DummyPipeline producing persisted AnalysisRuns.

**Architecture:** React + TypeScript renders the five V1 views and a reusable `SpectrogramViewer`; FastAPI owns data access and DSP; SQLite stores metadata/results; raw IQ and cached spectrum rasters remain on the local filesystem. The algorithm boundary is introduced early through a `Pipeline` contract and `LocalJobManager`, but the first executable pipeline is deterministic and CPU-only so the platform can be verified independently of research-model code.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x, Pydantic 2, NumPy, SciPy, Matplotlib, pytest, React 18+, TypeScript, Vite, Ant Design, React Router, Vitest, React Testing Library, SQLite, local filesystem.

**Spec:** `docs/superpowers/specs/2026-09-04-wideband-signal-platform-v1-design.md`

## Global Constraints

- Repository: `git@gitee.com:liguanglai/wideband-intelligent-signal-analysis-platform.git`.
- Current repository contents were not inspectable from the planning environment because outbound access to Gitee was unavailable; Task 1 is therefore a hard compatibility gate before scaffolding.
- V1 is local-first and CPU-first; a local NVIDIA GPU is not required.
- Do not add Redis, Celery, RQ, PostgreSQL, MinIO, Docker, Kubernetes, WebSocket, or microservices in this plan.
- Frontend never performs core DSP and never receives raw full-recording IQ arrays.
- All platform-level detection and GT coordinates use seconds and Hz; pixel coordinates are view-only.
- `DetectionResult` is mandatory pipeline output; intermediate artifacts are optional.
- SpaceNet-specific parsing belongs only in the future `SpaceNetAdapter`; this core plan only installs the canonical `spacenet_14` label mapping.
- Heavy GPU runs will be integrated through Analysis Packages in a separate implementation plan.
- Preserve raw files; use relative storage references and a centralized storage service instead of hard-coded absolute paths.
- Use REST + polling for run status; no WebSocket/SSE in the core slice.
- Keep files focused; if an implementation file exceeds roughly 300 lines during execution, split by responsibility before adding more behavior.

---

## Target File Structure for This Plan

The executor must first compare this target with the repository. If non-trivial existing code conflicts with these paths or uses another established framework, stop after Task 1 and request a plan adjustment rather than overwriting it.

```text
wideband-intelligent-signal-analysis-platform/
├── ARCHITECTURE.md
├── V1_SCOPE.md
├── README.md
├── platform.db                         # runtime-generated, gitignored
├── label_spaces/
│   └── spacenet_14.json
├── data/                               # runtime-generated content, gitignored except .gitkeep
│   ├── recordings/.gitkeep
│   ├── artifacts/.gitkeep
│   ├── imports/.gitkeep
│   └── cache/spectrograms/.gitkeep
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   └── errors.py
│   │   ├── db/
│   │   │   ├── base.py
│   │   │   └── session.py
│   │   ├── storage/
│   │   │   └── service.py
│   │   ├── labels/
│   │   │   └── service.py
│   │   ├── recordings/
│   │   │   ├── model.py
│   │   │   ├── schema.py
│   │   │   ├── service.py
│   │   │   └── router.py
│   │   ├── ground_truth/
│   │   │   ├── model.py
│   │   │   ├── schema.py
│   │   │   ├── service.py
│   │   │   └── router.py
│   │   ├── dsp/
│   │   │   ├── iq.py
│   │   │   ├── stft.py
│   │   │   └── router.py
│   │   ├── detections/
│   │   │   ├── model.py
│   │   │   ├── schema.py
│   │   │   ├── service.py
│   │   │   └── router.py
│   │   ├── analysis/
│   │   │   ├── model.py
│   │   │   ├── schema.py
│   │   │   ├── service.py
│   │   │   ├── job_manager.py
│   │   │   ├── worker.py
│   │   │   └── router.py
│   │   └── pipelines/
│   │       ├── base.py
│   │       ├── registry.py
│   │       └── dummy.py
│   └── tests/
│       ├── conftest.py
│       ├── test_health.py
│       ├── test_label_space.py
│       ├── test_recordings.py
│       ├── test_stft.py
│       ├── test_ground_truth.py
│       ├── test_detections.py
│       └── test_analysis_runs.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── src/
│   │   ├── main.tsx
│   │   ├── app/App.tsx
│   │   ├── app/MainLayout.tsx
│   │   ├── api/client.ts
│   │   ├── api/types.ts
│   │   ├── mocks/demo.ts
│   │   ├── pages/RecordingsPage.tsx
│   │   ├── pages/SpectrumAnalysisPage.tsx
│   │   ├── pages/SignalsPage.tsx
│   │   ├── pages/SignalDetailPage.tsx
│   │   ├── pages/AlgorithmLabPage.tsx
│   │   ├── features/spectrum/SpectrogramViewer.tsx
│   │   ├── features/spectrum/coordinates.ts
│   │   ├── features/signals/SignalResultsPanel.tsx
│   │   └── features/signals/SignalSummary.tsx
│   └── src/**/*.test.ts(x)
└── tests/
    └── fixtures/
        ├── tiny_iq_complex64.bin
        ├── tiny_ground_truth.json
        └── demo_detections.json
```

---

### Task 1: Repository compatibility gate and project guardrails

**Files:**
- Inspect: repository root and existing tracked files
- Create only if absent and compatible: `ARCHITECTURE.md`
- Create only if absent and compatible: `V1_SCOPE.md`
- Create only if absent and compatible: `.gitignore`
- Create only if absent and compatible: `data/recordings/.gitkeep`
- Create only if absent and compatible: `data/artifacts/.gitkeep`
- Create only if absent and compatible: `data/imports/.gitkeep`
- Create only if absent and compatible: `data/cache/spectrograms/.gitkeep`

**Interfaces:**
- Consumes: approved design spec at `docs/superpowers/specs/2026-09-04-wideband-signal-platform-v1-design.md`
- Produces: repository guardrails every later task must follow

- [ ] **Step 1: Audit the actual repository before creating files**

Run:

```bash
git status --short
git branch --show-current
find . -maxdepth 3 -type f -not -path './.git/*' | sort | head -200
```

Expected: repository state and existing conventions are visible. If there is substantial existing application code under paths that conflict with the target structure, stop here and report the conflicts; do not overwrite or scaffold around them silently.

- [ ] **Step 2: Verify the approved design document exists in the repository**

Run:

```bash
test -f docs/superpowers/specs/2026-09-04-wideband-signal-platform-v1-design.md
```

Expected: exit status 0. If it is absent, copy the approved spec into that exact path before continuing and include it in the first commit.

- [ ] **Step 3: Write `ARCHITECTURE.md` with the non-negotiable architecture rules**

Use this content skeleton verbatim, expanding only with links to the spec:

```markdown
# Architecture Guardrails

1. V1 is local-first and CPU-first.
2. React/TypeScript is the UI; FastAPI owns data access and DSP.
3. Raw IQ stays in backend/local storage; frontend receives display-sized data only.
4. Platform coordinates are seconds + Hz, never pixel-only boxes.
5. A Pipeline may be internally arbitrary but must emit DetectionResult[].
6. Intermediate artifacts are optional.
7. SQLite stores structured metadata/results; large arrays/files stay on disk.
8. Heavy GPU results enter through Analysis Packages; no local CUDA requirement.
9. SpaceNet-specific parsing stays inside SpaceNetAdapter/LabelSpace code.
10. Do not add Redis/Celery/RQ/PostgreSQL/MinIO/Docker/Kubernetes without explicit approval.
11. Do not add model training, SDR streaming, geolocation, demodulation, or protocol decoding to V1.
12. Prefer a visible vertical slice over speculative infrastructure.
```

- [ ] **Step 4: Write `V1_SCOPE.md` with explicit in/out scope**

Include two sections whose bullets match the approved spec: `V1 includes` and `V1 excludes`. `V1 includes` must name offline IQ, STFT/LS-STFT, detection/localization, 14-class SpaceNet support, Signal views, GT comparison, Algorithm Lab, and imported GPU results. `V1 excludes` must name real-time SDR, DOA/TDOA/FDOA, geolocation, demodulation/protocol decode, training platform, multi-user, and distributed infrastructure.

- [ ] **Step 5: Add runtime paths to `.gitignore` without ignoring fixtures or source schemas**

Ensure these patterns exist:

```gitignore
platform.db
.venv/
__pycache__/
.pytest_cache/
frontend/node_modules/
frontend/dist/
data/recordings/*
!data/recordings/.gitkeep
data/artifacts/*
!data/artifacts/.gitkeep
data/imports/*
!data/imports/.gitkeep
data/cache/*
!data/cache/spectrograms/
data/cache/spectrograms/*
!data/cache/spectrograms/.gitkeep
```

- [ ] **Step 6: Create the runtime directory placeholders and verify no raw data is staged**

Run:

```bash
mkdir -p data/recordings data/artifacts data/imports data/cache/spectrograms
touch data/recordings/.gitkeep data/artifacts/.gitkeep data/imports/.gitkeep data/cache/spectrograms/.gitkeep
git status --short
```

Expected: only guardrail/docs/gitkeep files are candidates for commit; no IQ/model data is staged.

- [ ] **Step 7: Commit the repository guardrails**

```bash
git add docs/superpowers/specs/2026-09-04-wideband-signal-platform-v1-design.md ARCHITECTURE.md V1_SCOPE.md .gitignore data
git commit -m "docs: lock v1 architecture and scope"
```

---

### Task 2: Scaffold the backend and canonical SpaceNet label space

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/errors.py`
- Create: `backend/app/db/base.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/labels/service.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`
- Create: `backend/tests/test_label_space.py`
- Create: `label_spaces/spacenet_14.json`

**Interfaces:**
- Consumes: architecture guardrails from Task 1
- Produces: `app.main.app`, `Settings`, SQLAlchemy session factory, `LabelSpaceService.get("spacenet_14")`

- [ ] **Step 1: Write the failing health and label-space tests**

`backend/tests/test_health.py`:

```python
def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

`backend/tests/test_label_space.py`:

```python
from app.labels.service import LabelSpaceService


def test_spacenet_14_mapping(settings):
    labels = LabelSpaceService(settings.label_space_root).get("spacenet_14")
    assert len(labels.classes) == 14
    assert labels.classes[0].name == "WiFi 20MHz QPSK"
    assert labels.classes[9].name == "LoRa 250kHz"
    assert labels.classes[13].name == "FM"
```

- [ ] **Step 2: Create `backend/pyproject.toml` and install the backend in editable mode**

Use dependencies equivalent to:

```toml
[project]
name = "wideband-signal-platform-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115,<1",
  "uvicorn[standard]>=0.30,<1",
  "sqlalchemy>=2.0,<3",
  "pydantic-settings>=2.5,<3",
  "python-multipart>=0.0.9,<1",
  "numpy>=1.26,<3",
  "scipy>=1.13,<2",
  "matplotlib>=3.8,<4"
]

[project.optional-dependencies]
dev = ["pytest>=8,<9", "httpx>=0.27,<1"]
```

Run:

```bash
python -m venv .venv
. .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate
python -m pip install -e "./backend[dev]"
```

Expected: install succeeds without CUDA, Redis, database server, or Docker dependencies.

- [ ] **Step 3: Create the canonical `spacenet_14.json` mapping**

Use exactly these ids and names:

```json
{
  "id": "spacenet_14",
  "version": 1,
  "classes": [
    {"id": 0, "name": "WiFi 20MHz QPSK"},
    {"id": 1, "name": "WiFi 20MHz 16QAM"},
    {"id": 2, "name": "WiFi 20MHz 64QAM"},
    {"id": 3, "name": "WiFi 40MHz QPSK"},
    {"id": 4, "name": "WiFi 40MHz 16QAM"},
    {"id": 5, "name": "WiFi 40MHz 64QAM"},
    {"id": 6, "name": "BLE LE1M"},
    {"id": 7, "name": "BLE LE2M"},
    {"id": 8, "name": "Zigbee"},
    {"id": 9, "name": "LoRa 250kHz"},
    {"id": 10, "name": "SRRC QPSK"},
    {"id": 11, "name": "SRRC 16QAM"},
    {"id": 12, "name": "AM"},
    {"id": 13, "name": "FM"}
  ]
}
```

- [ ] **Step 4: Implement settings, database session, label service, and health API minimally**

Required public signatures:

```python
# backend/app/core/config.py
class Settings(BaseSettings):
    project_root: Path
    data_root: Path
    label_space_root: Path
    database_url: str

# backend/app/db/session.py
def get_session() -> Iterator[Session]: ...

# backend/app/labels/service.py
@dataclass(frozen=True)
class LabelClass:
    id: int
    name: str

@dataclass(frozen=True)
class LabelSpace:
    id: str
    version: int
    classes: tuple[LabelClass, ...]

class LabelSpaceService:
    def __init__(self, root: Path): ...
    def get(self, label_space_id: str) -> LabelSpace: ...
```

`backend/app/main.py` must expose `GET /api/health` and configure CORS only for local Vite origins (`http://localhost:5173`, `http://127.0.0.1:5173`).

`backend/tests/conftest.py` must provide isolated fixtures with no writes to the real project data directory:

```python
@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_root = tmp_path / "data"
    label_root = Path(__file__).resolve().parents[2] / "label_spaces"
    return Settings(
        project_root=tmp_path,
        data_root=data_root,
        label_space_root=label_root,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )

@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
```

Expose `create_app(settings: Settings | None = None) -> FastAPI` from `app.main` so tests can inject isolated settings.

- [ ] **Step 5: Run the focused backend tests**

Run:

```bash
pytest backend/tests/test_health.py backend/tests/test_label_space.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit the backend foundation**

```bash
git add backend label_spaces/spacenet_14.json
git commit -m "feat: add backend foundation and label space"
```

---

### Task 3: Build M0 five-page frontend shell with mock data

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/MainLayout.tsx`
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/mocks/demo.ts`
- Create: five page files listed in target structure
- Create: `frontend/src/features/spectrum/SpectrogramViewer.tsx`
- Create: `frontend/src/features/spectrum/coordinates.ts`
- Create: `frontend/src/features/signals/SignalResultsPanel.tsx`
- Create: `frontend/src/features/signals/SignalSummary.tsx`
- Test: `frontend/src/app/App.test.tsx`
- Test: `frontend/src/features/spectrum/SpectrogramViewer.test.tsx`

**Interfaces:**
- Consumes: none from backend; uses mock data only
- Produces: typed `DetectionResult`, `RecordingSummary`, `SpectrogramMeta` frontend contracts and interactive page navigation

- [ ] **Step 1: Scaffold Vite React TypeScript and install only approved UI dependencies**

Run:

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install antd react-router-dom
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
```

Do not add Redux, React Query, ECharts, Canvas libraries, or a state-management framework in M0.

- [ ] **Step 2: Define API/view types used by mocks and later real APIs**

`frontend/src/api/types.ts` must include:

```ts
export interface DetectionResult {
  id: string;
  tStartS: number;
  tEndS: number;
  fLowHz: number;
  fHighHz: number;
  classId: number;
  className: string;
  confidence: number;
}

export interface SpectrogramMeta {
  imageUrl: string;
  tStartS: number;
  tEndS: number;
  fLowHz: number;
  fHighHz: number;
  representation: "stft" | "ls-stft";
}

export interface RecordingSummary {
  id: string;
  name: string;
  datasetName: string | null;
  sampleRateHz: number;
  centerFrequencyHz: number;
  durationS: number;
  hasGroundTruth: boolean;
}
```

- [ ] **Step 3: Write the failing app-shell and viewer tests**

App test must verify navigation labels `Recordings`, `Spectrum Analysis`, `Signals`, `Algorithm Lab`, `Settings` are visible and Recordings is the default route.

Viewer test must render one mock detection and verify clicking its overlay invokes `onSelectDetection("det_002")`.

- [ ] **Step 4: Implement the page shell and routes**

Routes:

```text
/recordings
/spectrum/:recordingId
/signals/:runId
/signals/:runId/:detectionId
/algorithm-lab
```

`/` redirects to `/recordings`. `Settings` may render a static V1 message but must not add configuration functionality.

- [ ] **Step 5: Implement a reusable image-plus-SVG `SpectrogramViewer`**

Required props:

```ts
interface SpectrogramViewerProps {
  meta: SpectrogramMeta;
  detections: DetectionResult[];
  selectedDetectionId?: string;
  onSelectDetection?: (id: string) => void;
}
```

Use percentage positioning from physical coordinates, not stored pixel coordinates:

```ts
export const timeToPercent = (t: number, start: number, end: number) =>
  ((t - start) / (end - start)) * 100;

export const frequencyToPercentFromTop = (f: number, low: number, high: number) =>
  ((high - f) / (high - low)) * 100;
```

The mock viewer may use a CSS-generated placeholder background; the contract must already accept a future `imageUrl`.

- [ ] **Step 6: Implement mock interactions across Spectrum, Signals, and Detail**

Use one shared mock run containing at least three detections from different `spacenet_14` classes. Single-click selects a signal in Spectrum; `View Details` navigates to Signal Detail; `View All` navigates to Signals; `Show in Spectrum` navigates back with the selected detection id in query state.

- [ ] **Step 7: Run frontend tests and production build**

Run:

```bash
cd frontend
npm test -- --run
npm run build
```

Expected: tests and TypeScript production build pass.

- [ ] **Step 8: Commit M0**

```bash
git add frontend
git commit -m "feat: add mock v1 frontend workflow"
```

---

### Task 4: Add SQLite domain models and centralized storage service

**Files:**
- Create: `backend/app/storage/service.py`
- Create: `backend/app/recordings/model.py`
- Create: `backend/app/ground_truth/model.py`
- Create: `backend/app/detections/model.py`
- Create: `backend/app/analysis/model.py`
- Modify: `backend/app/db/base.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_recordings.py`

**Interfaces:**
- Consumes: `Settings`, SQLAlchemy session from Task 2
- Produces: persisted `RecordingModel`, `GroundTruthModel`, `AnalysisRunModel`, `DetectionResultModel`; `StorageService`

- [ ] **Step 1: Write a failing persistence test for the four core entities**

The test must create one Recording, one completed AnalysisRun, one GT, and one DetectionResult and assert relationships can be read in a fresh session.

Use statuses exactly from the spec: `pending`, `running`, `completed`, `failed`, `interrupted`.

- [ ] **Step 2: Implement `StorageService` before feature services start handling paths**

Required interface:

```python
class StorageService:
    def __init__(self, data_root: Path): ...
    def recording_dir(self, recording_id: str) -> Path: ...
    def artifact_dir(self, run_id: str) -> Path: ...
    def spectrogram_cache_dir(self) -> Path: ...
    def import_temp_dir(self, token: str) -> Path: ...
```

Every method must return a path below `data_root`; reject path traversal input containing separators or `..`.

- [ ] **Step 3: Implement SQLAlchemy models with string public ids**

Use prefixed string ids (`rec_...`, `run_...`, `gt_...`, `det_...`) as primary keys so file paths and imported packages do not depend on SQLite auto-increment ids.

Required core fields must match the spec. `RecordingModel.data_path` is stored relative to project/data root. `DetectionResultModel` stores seconds/Hz directly. `AnalysisRunModel` must also include nullable `error_type`, `error_message`, and `worker_pid` so subprocess failures and restart recovery are persisted without a second schema change in Task 9.

- [ ] **Step 4: Initialize SQLite schema on app startup**

For this core slice use `Base.metadata.create_all`; do not introduce Alembic yet. Use a file database at repository root `platform.db` in normal runs and an isolated temporary SQLite DB in tests.

- [ ] **Step 5: Run the persistence test**

```bash
pytest backend/tests/test_recordings.py -v
```

Expected: PASS and no database-server dependency.

- [ ] **Step 6: Commit the domain/storage foundation**

```bash
git add backend/app/storage backend/app/recordings/model.py backend/app/ground_truth/model.py backend/app/detections/model.py backend/app/analysis/model.py backend/app/db backend/app/main.py backend/tests/test_recordings.py
git commit -m "feat: add sqlite domain and storage models"
```

---

### Task 5: Implement custom complex64 Recording import and listing (M1)

**Files:**
- Create: `backend/app/recordings/schema.py`
- Create: `backend/app/recordings/service.py`
- Create: `backend/app/recordings/router.py`
- Create: `backend/app/dsp/iq.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_recordings.py`
- Create fixture: `tests/fixtures/tiny_iq_complex64.bin`

**Interfaces:**
- Consumes: `RecordingModel`, `StorageService`
- Produces: `POST /api/recordings`, `GET /api/recordings`, `GET /api/recordings/{id}`, `read_iq(recording) -> np.ndarray[np.complex64]`

- [ ] **Step 1: Generate a deterministic tiny IQ fixture for tests**

Create 4096 complex samples using two complex sinusoids and write little-endian complex64. The generation script used once in the test fixture setup must be equivalent to:

```python
n = np.arange(4096, dtype=np.float32)
x = np.exp(2j * np.pi * 0.08 * n) + 0.4 * np.exp(2j * np.pi * 0.22 * n)
x.astype("<c8").tofile(path)
```

The committed fixture must remain small.

- [ ] **Step 2: Write failing API tests for import, list, and detail**

Upload the fixture with form fields:

```text
name=tiny-demo
sample_rate_hz=1000000
center_frequency_hz=2441000000
data_format=complex64_le
```

Assert returned `num_samples == 4096`, `duration_s == 0.004096`, `frequency_low_hz == 2440500000`, and `frequency_high_hz == 2441500000`.

- [ ] **Step 3: Implement temp → validate → commit import flow**

`RecordingService.import_complex64(...)` must:

1. stream upload into `data/imports/<token>/source.bin`;
2. reject file size 0 or byte size not divisible by 8;
3. require positive sample rate;
4. derive `num_samples`, `duration_s`, and frequency limits;
5. move the complete file into `data/recordings/<recording_id>/raw.iq`;
6. create the SQLite row only after the move succeeds;
7. clean the temp directory on validation failure.

- [ ] **Step 4: Implement IQ reading with explicit format handling**

Required interface:

```python
def read_iq(recording: RecordingModel, data_root: Path, start_sample: int = 0, count: int | None = None) -> np.ndarray:
    ...
```

For `complex64_le`, use `np.memmap(..., dtype="<c8")` and return a copied NumPy array for the requested segment. Reject unsupported formats with `INVALID_RECORDING` rather than guessing.

- [ ] **Step 5: Wire routes and run tests**

```bash
pytest backend/tests/test_recordings.py -v
```

Expected: import/list/detail and validation cases pass.

- [ ] **Step 6: Manually start the API and verify it remains CPU-only**

```bash
uvicorn app.main:app --app-dir backend --reload
```

Expected: `/api/health` and `/api/recordings` respond; no CUDA initialization occurs.

- [ ] **Step 7: Commit M1 backend**

```bash
git add backend tests/fixtures/tiny_iq_complex64.bin
git commit -m "feat: import and manage local iq recordings"
```

---

### Task 6: Compute, cache, and display a real STFT spectrogram (M2)

**Files:**
- Create: `backend/app/dsp/stft.py`
- Create: `backend/app/dsp/router.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_stft.py`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx`
- Modify: `frontend/src/features/spectrum/SpectrogramViewer.tsx`
- Test: `frontend/src/features/spectrum/SpectrogramViewer.test.tsx`

**Interfaces:**
- Consumes: Recording + `read_iq()` from Task 5
- Produces: `GET /api/recordings/{id}/spectrogram?representation=stft`, cached matrix/raster, real Spectrum Analysis view

- [ ] **Step 1: Write a failing DSP test that checks the frequency axis in physical Hz**

Use the tiny fixture at sample rate 1 MHz and center frequency 2.441 GHz. Assert the returned STFT metadata frequency bounds are centered on 2.441 GHz and that the dominant bins are near the two injected offsets within one FFT bin.

- [ ] **Step 2: Implement a typed STFT result**

Required interface:

```python
@dataclass(frozen=True)
class SpectrogramResult:
    magnitude_db: np.ndarray
    time_axis_s: np.ndarray
    frequency_axis_hz: np.ndarray


def compute_stft(
    iq: np.ndarray,
    sample_rate_hz: float,
    center_frequency_hz: float,
    nperseg: int = 512,
    noverlap: int = 256,
    nfft: int = 512,
) -> SpectrogramResult:
    ...
```

Use `scipy.signal.stft(..., return_onesided=False)`, FFT-shift the frequency dimension, add center frequency, and convert magnitude to dB with an epsilon floor.

- [ ] **Step 3: Cache both research matrix and Web preview**

For a request, compute a stable cache key from recording id + representation + STFT parameters. Write:

```text
data/cache/spectrograms/<key>.npz   # magnitude_db, time_axis_s, frequency_axis_hz
data/cache/spectrograms/<key>.png   # browser preview
```

The API response must contain a media URL plus:

```json
{
  "representation": "stft",
  "t_start_s": 0.0,
  "t_end_s": 0.004096,
  "f_low_hz": 2440500000.0,
  "f_high_hz": 2441500000.0
}
```

Do not send the full matrix as JSON.

- [ ] **Step 4: Serve cached media read-only through FastAPI**

Mount only the intended cache/artifact directory with `StaticFiles`; do not expose the repository root or raw IQ directory as a generic static mount.

- [ ] **Step 5: Replace the mock spectrum image with the real API result**

`SpectrumAnalysisPage` loads Recording detail, then STFT metadata. The same `SpectrogramViewer` from M0 renders the returned image; existing physical-coordinate overlay code must remain unchanged.

- [ ] **Step 6: Add minimal pointer readout and image zoom/pan without a heavy charting library**

Implement viewer-local zoom scale and pan offset with pointer/wheel events. Cursor readout must map pointer position back to seconds/Hz using current inverse view transform and the `SpectrogramMeta` bounds. Clamp zoom to a finite range such as `1..8` and provide a `Reset View` button.

- [ ] **Step 7: Run backend and frontend tests/build**

```bash
pytest backend/tests/test_stft.py -v
cd frontend && npm test -- --run && npm run build
```

Expected: DSP axis test, viewer interaction tests, and build pass.

- [ ] **Step 8: Commit M2**

```bash
git add backend/app/dsp backend/app/main.py backend/tests/test_stft.py frontend/src
git commit -m "feat: render real stft spectrum workspace"
```

---

### Task 7: Add Ground Truth, persisted detections, Signals, and Signal Detail (M3)

**Files:**
- Create: `backend/app/ground_truth/schema.py`
- Create: `backend/app/ground_truth/service.py`
- Create: `backend/app/ground_truth/router.py`
- Create: `backend/app/detections/schema.py`
- Create: `backend/app/detections/service.py`
- Create: `backend/app/detections/router.py`
- Modify: `backend/app/dsp/router.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_ground_truth.py`
- Create: `backend/tests/test_detections.py`
- Create: `tests/fixtures/tiny_ground_truth.json`
- Create: `tests/fixtures/demo_detections.json`
- Modify: Spectrum/Signals/Detail frontend files

**Interfaces:**
- Consumes: Recording, STFT viewer, SQLite entities
- Produces: GT API, DetectionResult API, waveform/FFT display endpoints, complete product-analysis view chain

- [ ] **Step 1: Define platform-native GT and detection fixtures in seconds + Hz**

Use the canonical shapes:

```json
{
  "label_space": "spacenet_14",
  "objects": [
    {
      "id": "gt_001",
      "t_start_s": 0.0005,
      "t_end_s": 0.0035,
      "f_low_hz": 2441060000.0,
      "f_high_hz": 2441100000.0,
      "class_id": 6,
      "class_name": "BLE LE1M"
    }
  ]
}
```

The detection fixture uses the same coordinate units plus `confidence`.

- [ ] **Step 2: Write failing validation tests**

Tests must reject:

- `t_start_s >= t_end_s`;
- `f_low_hz >= f_high_hz`;
- class id/name mismatch against `spacenet_14`;
- boxes outside the Recording time/frequency extent.

Tests must explicitly allow `t_start_s == 0` and `t_end_s == recording.duration_s`.

- [ ] **Step 3: Implement GT import API using the canonical LabelSpace service**

Required route:

```text
POST /api/recordings/{recording_id}/ground-truth
GET  /api/recordings/{recording_id}/ground-truth
```

Persist class id and class name after validating against `spacenet_14`; do not accept an unknown mapping silently.

- [ ] **Step 4: Implement persisted DetectionResult read APIs and demo loader**

Required routes:

```text
GET /api/analysis-runs/{run_id}/detections
GET /api/detections/{detection_id}
```

Create `scripts/load_demo_results.py` to create a completed demo run from `tests/fixtures/demo_detections.json`; keep this as a developer fixture loader, not a production API. The script accepts `--recording-id` and prints the created `run_id`.

- [ ] **Step 5: Add display-sized I/Q and FFT endpoints for Signal Detail**

Required routes:

```text
GET /api/recordings/{recording_id}/waveform?t_start_s=...&t_end_s=...&max_points=4000
GET /api/detections/{detection_id}/fft?max_points=2048
```

Waveform response returns separate I and Q arrays after deterministic stride/downsampling when needed. FFT reads only the detection time segment, returns a display-sized frequency/magnitude series, and never sends full raw IQ to the browser.

- [ ] **Step 6: Replace mock signal data in the three product views with API data**

Spectrum Analysis overlays both Prediction and optional GT. Signals renders a sortable/filterable Ant Design table with derived center frequency, bandwidth, and duration. Signal Detail renders summary plus Local Spectrogram, I/Q waveform, and FFT; PSD may reuse FFT magnitude in this core slice rather than adding a second estimator.

- [ ] **Step 7: Add shared front-end derivation helpers instead of duplicating formulas**

Create functions equivalent to:

```ts
export const centerFrequencyHz = (d: DetectionResult) => (d.fLowHz + d.fHighHz) / 2;
export const bandwidthHz = (d: DetectionResult) => d.fHighHz - d.fLowHz;
export const durationS = (d: DetectionResult) => d.tEndS - d.tStartS;
```

Use them in Spectrum panel, Signals table, and Signal Detail.

- [ ] **Step 8: Run the M3 tests/build**

```bash
pytest backend/tests/test_ground_truth.py backend/tests/test_detections.py -v
cd frontend && npm test -- --run && npm run build
```

Expected: validation and view tests pass.

- [ ] **Step 9: Commit M3**

```bash
git add backend frontend/src tests/fixtures scripts/load_demo_results.py
git commit -m "feat: add detection and signal analysis views"
```

---

### Task 8: Introduce Pipeline contract, AnalysisRun lifecycle, and DummyPipeline subprocess (M4)

**Files:**
- Create: `backend/app/pipelines/base.py`
- Create: `backend/app/pipelines/registry.py`
- Create: `backend/app/pipelines/dummy.py`
- Create: `backend/app/analysis/schema.py`
- Create: `backend/app/analysis/service.py`
- Create: `backend/app/analysis/job_manager.py`
- Create: `backend/app/analysis/worker.py`
- Create: `backend/app/analysis/router.py`
- Modify: `backend/app/main.py`
- Create/Modify: `backend/tests/test_analysis_runs.py`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx`

**Interfaces:**
- Consumes: Recording, DetectionResult persistence, StorageService
- Produces: `Pipeline` contract, registry, `POST /api/analysis-runs`, polling status API, subprocess execution

- [ ] **Step 1: Write failing Pipeline contract tests**

Define the required immutable definitions:

```python
@dataclass(frozen=True)
class PipelineDefinition:
    id: str
    name: str
    version: str
    label_space: str
    recommended_device: str
    cpu_supported: bool
    stages: tuple[str, ...]
    inspectable_stages: tuple[str, ...]

@dataclass(frozen=True)
class RecordingInput:
    id: str
    data_path: Path
    data_format: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    duration_s: float
    label_space: str | None

@dataclass(frozen=True)
class DetectionPayload:
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str
    confidence: float
    scores: dict[str, float] | None = None

@dataclass(frozen=True)
class ArtifactPayload:
    stage_name: str
    artifact_type: str
    scope: str
    path: Path
    detection_index: int | None = None
    metadata: dict[str, Any] | None = None

@dataclass
class PipelineOutput:
    detections: list[DetectionPayload]
    artifacts: list[ArtifactPayload]
    run_metadata: dict[str, Any]

class Pipeline(ABC):
    @property
    @abstractmethod
    def definition(self) -> PipelineDefinition: ...

    @abstractmethod
    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput: ...
```

Test that `DummyPipeline.definition.id == "dummy"`, `cpu_supported is True`, and the run returns at least one physically valid `DetectionPayload` within the supplied Recording bounds.

- [ ] **Step 2: Implement a registry with explicit registration, not dynamic filesystem magic**

Required interface:

```python
class PipelineRegistry:
    def __init__(self, pipelines: Iterable[Pipeline]): ...
    def list(self) -> list[PipelineDefinition]: ...
    def get(self, pipeline_id: str) -> Pipeline: ...
```

Register only `DummyPipeline` in this plan.

- [ ] **Step 3: Write failing AnalysisRun lifecycle tests**

Test:

1. `POST /api/analysis-runs` with `recording_id`, `pipeline_id="dummy"`, `executor="local_cpu"` returns a run in `pending` or `running`;
2. polling eventually returns `completed`;
3. the completed run has persisted DetectionResults;
4. invalid pipeline id returns `PIPELINE_INCOMPATIBLE` or a specific not-found business error, never a Python traceback.

- [ ] **Step 4: Implement the validation and run creation service**

`AnalysisService.create_run(...)` must verify the Recording exists, Pipeline exists, label spaces are compatible, and `cpu_supported` is true for `local_cpu`. It creates the DB row before scheduling, but does not execute model code inside the HTTP request.

- [ ] **Step 5: Implement `LocalJobManager` with a separate Python process**

Required interface:

```python
class LocalJobManager:
    def start(self, run_id: str) -> int:
        """Spawn `python -m app.analysis.worker <run_id>` and return PID."""
```

Use `sys.executable`, pass `run_id` as an argv element, set `cwd` to the backend project directory, and avoid `shell=True`.

- [ ] **Step 6: Implement the worker transaction flow**

Worker behavior:

```text
load AnalysisRun -> mark running -> load Recording -> run Pipeline
-> validate every DetectionPayload against Recording + LabelSpace
-> replace/persist run detections -> persist optional artifacts metadata
-> mark completed
```

On unhandled exception: rollback the current unit of work, open a fresh session, mark run `failed`, store a concise `error_type` + `error_message`, and log the full traceback server-side.

- [ ] **Step 7: Add REST + polling endpoints**

Required routes:

```text
GET  /api/pipelines
POST /api/analysis-runs
GET  /api/analysis-runs/{run_id}
GET  /api/analysis-runs/{run_id}/detections
```

Do not add WebSocket/SSE.

- [ ] **Step 8: Wire Spectrum Analysis `Run Analysis` to DummyPipeline**

When the user runs DummyPipeline, create a run, poll every 1 second while `pending/running`, stop polling on `completed/failed/interrupted`, then refresh detections. Disable duplicate submissions while a run is active.

- [ ] **Step 9: Run the complete core test suite and frontend build**

```bash
pytest backend/tests -v
cd frontend && npm test -- --run && npm run build
```

Expected: all core tests pass without any GPU or external services.

- [ ] **Step 10: Commit M4**

```bash
git add backend frontend/src
git commit -m "feat: execute analysis runs through pipeline contract"
```

---

### Task 9: Add failure recovery and core smoke verification

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/analysis/service.py`
- Modify: `backend/app/core/errors.py`
- Modify: `backend/tests/test_analysis_runs.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: completed M0–M4 core
- Produces: deterministic interrupted-run recovery, business-safe errors, documented local startup

- [ ] **Step 1: Add a failing startup-recovery test**

Create a run persisted as `running`, invoke the startup recovery function, and assert it becomes `interrupted` with a user-facing message indicating the prior local process ended.

- [ ] **Step 2: Implement startup recovery for stale local runs**

Required function:

```python
def mark_stale_running_runs_interrupted(session: Session) -> int:
    ...
```

Call it once during application startup. Do not attempt job continuation.

- [ ] **Step 3: Standardize business error response shape**

All handled platform errors must serialize as:

```json
{
  "error": {
    "code": "PIPELINE_INCOMPATIBLE",
    "message": "Selected pipeline cannot run for this recording.",
    "details": {}
  }
}
```

Keep traceback out of HTTP responses.

- [ ] **Step 4: Document exact local development commands in README**

Include Windows-friendly and POSIX-friendly commands for:

```text
python -m venv .venv
pip install -e "./backend[dev]"
uvicorn app.main:app --app-dir backend --reload
cd frontend
npm install
npm run dev
```

Also document that `platform.db` and `data/*` are local runtime state, and that this phase requires no NVIDIA GPU.

- [ ] **Step 5: Run the full verification gate**

Run:

```bash
pytest backend/tests -v
cd frontend && npm test -- --run && npm run build
```

Then manually verify this user flow with the tiny fixture:

```text
Recordings -> import tiny IQ -> Spectrum Analysis -> real STFT
-> Run DummyPipeline -> see BBox -> View All Signals -> View Details
-> enable GT overlay
```

Expected: every step works on CPU-only local environment.

- [ ] **Step 6: Commit the core vertical slice**

```bash
git add backend frontend README.md
git commit -m "chore: harden and document core v1 slice"
```

---

## Core Plan Exit Criteria

Do not begin SpaceNetAdapter, Analysis Package import, Algorithm Lab evaluation, End-to-End, or ZoomSpec implementation until all of these are true:

1. Backend tests pass from a clean CPU-only virtual environment.
2. Frontend tests and production build pass.
3. A custom complex64 Recording can be imported and reopened after restart.
4. Real STFT is rendered from that Recording and pointer coordinates report physical seconds/Hz.
5. Prediction and GT overlays use the same physical coordinate contract.
6. Signals and Signal Detail read the same persisted DetectionResults as Spectrum Analysis.
7. `POST /api/analysis-runs` executes DummyPipeline in a subprocess and survives worker failure without killing FastAPI.
8. A stale `running` run becomes `interrupted` after restart.
9. No Redis/Celery/PostgreSQL/Docker/CUDA dependency has entered the core path.
10. `ARCHITECTURE.md` and `V1_SCOPE.md` match the approved design spec.

## Follow-on Plans After This One

The approved V1 spec is intentionally split into additional plans rather than one oversized executor document:

- **Data & Research Integration Plan (M5–M8):** first real CPU Pipeline, Analysis Package import, SpaceNet advanced adapter using the verified 7500/2500 `.bin + .json` dataset contract, Algorithm Lab existing-run comparison.
- **Research Pipeline Integration Plan (M9–M10):** 2023 End-to-End pipeline adapter, ZoomSpec LS-STFT/CPN/AHLP/FRN integration, optional intermediate artifacts and Processing Inspector.

Those plans should be written only after the core exit criteria are met or when the user explicitly wants the full future plan before execution; this prevents later tasks from assuming repository details that the core implementation may establish differently.

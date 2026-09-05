# Wideband Intelligent Signal Analysis Platform

A local-first, CPU-friendly V1 workbench for offline wideband IQ analysis, time-frequency visualization, signal detection/localization, signal-class inspection, Ground Truth comparison, and research-pipeline integration.

The core V1 deliberately separates the Web platform from heavy GPU research workloads. Local CPU execution is supported for DSP and lightweight pipelines; future heavy AutoDL/GPU results enter through the Analysis Package workflow described in the design spec.

## Current core slice (M0–M4)

- React + TypeScript five-page shell: Recordings, Spectrum Analysis, Signals, Signal Detail, Algorithm Lab.
- FastAPI REST backend.
- SQLite metadata/results database.
- Local filesystem storage for raw IQ and spectrogram cache.
- Custom little-endian `complex64` IQ import.
- Real STFT computation with physical time/frequency coordinates.
- Ground Truth and DetectionResult data in seconds + Hz.
- Display-sized I/Q waveform and FFT endpoints.
- Pipeline contract and deterministic CPU-only DummyPipeline.
- CPU STFT energy detector pipeline (`stft_energy_detector`) for detection/localization only; it is not a 14-class recognizer.
- AnalysisRun lifecycle with a separate Python subprocess and REST polling.
- Startup recovery that marks stale `running` jobs as `interrupted`.

No NVIDIA GPU, CUDA, Redis, Celery, PostgreSQL, Docker, or external service is required for this phase.

## Prerequisites

- Python 3.11+
- Node.js 18+
- npm

## Backend setup

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[dev]"
uvicorn app.main:app --app-dir backend --reload
```

### POSIX shell

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e "./backend[dev]"
uvicorn app.main:app --app-dir backend --reload
```

The API defaults to `http://127.0.0.1:8000`. Check:

```text
GET /api/health
```

## Frontend setup

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The Vite UI defaults to `http://127.0.0.1:5173` and calls the backend at `http://127.0.0.1:8000`. Override with `VITE_API_BASE_URL` if needed.

## Import a custom IQ recording

The V1 custom importer accepts little-endian complex64 samples (`<c8`): one complex sample is 8 bytes. Provide:

- Recording name
- Sample rate in Hz
- Center frequency in Hz
- IQ file
- Optional label space, normally `spacenet_14` for the current research path

Raw IQ stays under `data/recordings/` and is never exposed as a generic static Web directory.

## Local runtime state

The following are local runtime state and are intentionally gitignored:

```text
platform.db
data/recordings/*
data/artifacts/*
data/imports/*
data/cache/*
frontend/node_modules/
frontend/dist/
```

Do not commit research datasets, model weights, raw IQ captures, or generated caches.

## Tests

Backend:

```bash
pytest backend/tests -v
```

Frontend after `npm install`:

```bash
cd frontend
npm test -- --run
npm run build
```

## Architecture rules

Read these before asking a Coding Agent to modify the project:

- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`V1_SCOPE.md`](V1_SCOPE.md)
- [`docs/superpowers/specs/2026-09-04-wideband-signal-platform-v1-design.md`](docs/superpowers/specs/2026-09-04-wideband-signal-platform-v1-design.md)

The most important rule is: **algorithm internals may differ, but platform outputs use a unified physical-coordinate DetectionResult contract.**

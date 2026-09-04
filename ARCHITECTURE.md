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

See `docs/superpowers/specs/2026-09-04-wideband-signal-platform-v1-design.md` for the approved V1 design.

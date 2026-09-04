# V1 Scope

## V1 includes

- Offline IQ recording import and local-first analysis.
- STFT and LS-STFT representations.
- Wideband signal detection and time-frequency localization.
- Signal classification, including canonical SpaceNet 14-class support.
- Spectrum Analysis, Signals list, and Signal Detail views.
- Prediction and Ground Truth comparison in physical seconds + Hz coordinates.
- Algorithm Lab for controlled comparison of registered analysis pipelines.
- Imported GPU-generated analysis results through Analysis Packages.
- CPU-friendly local execution for DSP and lightweight pipelines.

## V1 excludes

- Real-time SDR acquisition or live streaming.
- DOA, TDOA, FDOA, emitter geolocation, or multi-station fusion.
- Full demodulation or protocol decoding.
- Model training, hyperparameter tuning, AutoML, or distributed training.
- Multi-user authentication, permissions, or tenant isolation.
- Distributed infrastructure such as Redis, Celery, RQ, PostgreSQL, MinIO, Docker, Kubernetes, or microservices.

# Algorithm Integration Standard v1 — Design Specification

**Date:** 2026-09-06  
**Status:** Proposed v1 standard for the Wideband Intelligent Signal Analysis Platform  
**Scope:** Algorithm integration, reproducibility, execution interchange, and comparison for wideband signal detection/localization/classification pipelines.

---

## 1. Purpose

The platform solves one stable task family:

> Given a wideband IQ recording, detect signals in time-frequency space, localize each signal with a physical bounding box, and optionally identify its signal/system class.

Different algorithms may solve this task in fundamentally different ways. Examples include:

- Single-stage spectrogram detector: STFT/LS-STFT -> detector -> bbox + class.
- Multi-stage coarse-to-fine pipeline: representation -> detector -> DSP extraction/downconversion/filtering/decimation -> classifier/refiner -> final bbox + class.
- Detection-only DSP baseline: STFT -> energy segmentation -> bbox.
- Future raw-IQ CNN/RNN/Transformer pipelines.
- Future hybrid DSP + ML or traditional signal-processing pipelines.

The platform MUST NOT encode any one of these internal structures as the platform architecture. Instead, every algorithm adapts to a stable platform contract.

The governing principle is:

> **Algorithms adapt to the platform contract; the platform does not adapt its core model to each algorithm.**

---

## 2. Goals

Algorithm Integration Standard v1 establishes a common contract for:

1. Pipeline identity and versioning.
2. Pipeline input from a platform Recording.
3. Final result output in physical time/frequency coordinates.
4. Detection-only and detection+classification task capabilities.
5. Label-space identity.
6. Optional intermediate artifacts without standardizing internal stages.
7. External/GPU execution through Analysis Package v1.
8. Reproducibility metadata for training and inference.
9. Comparable evaluation across heterogeneous algorithms.
10. Safe iteration when a model is tuned, replaced, or developed by another contributor.

---

## 3. Non-goals

v1 does NOT standardize:

- Neural-network architecture.
- Number of models in a pipeline.
- Number or names of internal stages.
- Training framework (Ultralytics, native PyTorch, Lightning, etc.).
- Exact spectrogram representation.
- Model weight packaging/distribution.
- Remote execution protocol.
- Live SDR streaming.
- Demodulation/protocol decoding.
- Experiment orchestration across thousands of recordings.

The standard intentionally constrains the boundaries, not the implementation.

---

## 4. Terminology

### 4.1 Model

A trainable or non-trainable computational component, e.g. YOLO, RT-DETR, FRN, CNN classifier, Transformer.

A model is NOT automatically a platform Pipeline.

### 4.2 Pipeline

A complete algorithmic solution from Recording input to platform-standard final output.

A Pipeline may contain:

- zero, one, or multiple neural networks;
- DSP preprocessing/postprocessing;
- STFT/LS-STFT/FFT/raw-IQ representations;
- proposal generation;
- heterodyne/downconversion;
- filtering/decimation;
- classification/refinement;
- thresholding/NMS/coordinate conversion.

The platform compares Pipeline Runs, not isolated model components.

### 4.3 Pipeline Adapter

A thin boundary layer that converts between platform contracts and algorithm-specific input/output.

The Adapter is responsible for:

- obtaining IQ through the platform Recording abstraction when running natively;
- converting algorithm coordinates to seconds + absolute Hz;
- mapping classes to the declared LabelSpace;
- normalizing final confidence to [0, 1];
- emitting optional artifacts/provenance.

Algorithm-specific logic should remain outside the platform core.

### 4.4 AnalysisRun

One execution of one Pipeline version on one Recording (or future scope). It captures immutable run metadata and final DetectionResults.

### 4.5 DetectionResult

The canonical platform result for one detected signal.

### 4.6 IntermediateArtifact

Optional pipeline output used for inspection/debugging/research. Artifacts are never required for basic result display.

### 4.7 LabelSpace

A stable identity for class-id/name mapping, e.g. `spacenet_14` or `signal_presence_v1`.

---

## 5. Pipeline Architecture Is Descriptive Metadata Only

A Pipeline may declare a human-readable architecture family, for example:

- `single_stage`
- `multi_stage`
- `hybrid`
- `dsp_baseline`
- `other`

This field is metadata for UI, documentation, and experiment interpretation.

**The platform MUST NOT branch execution logic based on architecture family.**

Forbidden platform design:

```text
if architecture_family == "multi_stage":
    call AHLP
```

Correct design:

```text
Pipeline Adapter
    -> internal implementation is free
    -> PipelineOutput
```

---

## 6. PipelineDefinition v1

Every integrated algorithm SHOULD expose metadata equivalent to:

```yaml
standard_version: 1
pipeline_id: spacenet_yolo26n_frn
pipeline_name: YOLOv26n + Combined FRN V3
pipeline_version: 1.0.0
label_space: spacenet_14
task_capability: detection_classification
architecture_family: multi_stage
executor_support:
  - external_import
stages:
  - representation
  - detector
  - signal_extraction
  - classifier_refiner
```

### Required semantic fields

- `standard_version`: Algorithm Integration Standard version. v1 = `1`.
- `pipeline_id`: stable machine-readable family identifier.
- `pipeline_name`: human-readable name.
- `pipeline_version`: immutable behavior version.
- `label_space`: identity of final class mapping.
- `task_capability`: final task capability.

### Recommended descriptive fields

- `architecture_family`
- `stages`
- `description`
- `author/team`
- supported executor types
- supported device/runtime notes

The optional `stages` list is descriptive. The platform does not require standard stage names or stage count.

---

## 7. Task Capability

v1 defines two primary capabilities:

### 7.1 `detection_localization`

The Pipeline detects and localizes signals but does not claim a modulation/system class.

Example: current STFT Energy Detector.

It MUST use a detection-only LabelSpace such as:

```text
signal_presence_v1
class_id = 0
class_name = "Signal"
```

It MUST NOT fabricate a SpaceNet class.

### 7.2 `detection_classification`

The Pipeline returns final signal bbox + signal/system class.

Example output LabelSpace:

```text
spacenet_14
```

Future capabilities may be added in later standard versions. v1 keeps the capability model intentionally small.

---

## 8. Pipeline Input Contract

### 8.1 Native/local execution

A native Pipeline receives a platform Recording abstraction, not a dataset-specific file format.

Conceptually:

```text
PipelineInput
├── Recording
│   ├── recording identity
│   ├── sample_rate_hz
│   ├── center_frequency_hz
│   ├── num_samples
│   ├── duration_s
│   ├── data_format
│   └── LabelSpace / dataset metadata when available
├── optional scope / ROI
├── runtime parameters
└── workspace / artifact directory
```

The Pipeline MUST obtain IQ through the common recording access layer (`RecordingReader` or its current platform equivalent).

The Pipeline MUST NOT require the platform core to know whether the underlying source is SpaceNet, custom complex64 IQ, or a future file format.

### 8.2 External/GPU execution

An external Pipeline may run outside the local platform (e.g. AutoDL). It does not receive the local database Recording object.

It MUST instead preserve sufficient recording identity/metadata to allow the resulting Analysis Package to be matched to a local Recording.

The external Pipeline MUST NOT assume a local platform `recording_id` is portable across machines.

---

## 9. Canonical Final Output Contract

Every Pipeline MUST eventually produce:

```text
PipelineOutput
└── detections: DetectionResult[]
```

Each DetectionResult MUST semantically contain:

```json
{
  "t_start_s": 0.032,
  "t_end_s": 0.080,
  "f_low_hz": 2417973850.0,
  "f_high_hz": 2418026150.0,
  "class_id": 9,
  "class_name": "LoRa 250kHz",
  "confidence": 0.94,
  "scores": {
    "detection": 0.91,
    "classification": 0.97
  }
}
```

`scores` is optional.

### 9.1 Physical-coordinate law

The platform contract is always:

- time in **seconds**, relative to Recording start;
- frequency in **absolute Hz**;
- lower bounds < upper bounds;
- bbox clipped/validated against Recording bounds where available.

Pixel coordinates, normalized xywh, MHz, kHz, milliseconds, FFT-bin indices, feature-map coordinates, and proposal-relative coordinates are implementation details and MUST NOT cross the final platform boundary.

### 9.2 Class law

- `class_id` MUST exist in the declared LabelSpace.
- `class_name` MUST agree with the LabelSpace mapping.
- Detection-only pipelines MUST declare/use a detection-only LabelSpace.
- Pipelines must never silently reinterpret one LabelSpace as another.

### 9.3 Confidence law

`confidence`:

- MUST be finite;
- MUST be in [0, 1];
- is the Pipeline's final confidence used by the platform;
- is not assumed to be probability-calibrated unless explicitly documented.

Multi-stage pipelines MAY provide component scores under `scores`, e.g. detection/classification/refinement confidence.

The platform MUST NOT invent a universal formula for combining stage scores.

---

## 10. Internal Heterogeneity and Adapters

### 10.1 Single-stage example

```text
Recording
  -> STFT
  -> YOLO/RT-DETR/D-FINE
  -> pixel bbox + class
  -> Adapter coordinate conversion
  -> DetectionResult[]
```

### 10.2 Multi-stage example

```text
Recording
  -> LS-STFT
  -> coarse detector
  -> proposal bbox
  -> heterodyne/downconvert
  -> bandwidth-matched filtering
  -> safe decimation
  -> classifier/refiner
  -> final bbox + class
  -> Adapter
  -> DetectionResult[]
```

### 10.3 Raw-IQ model example

```text
Recording
  -> raw IQ model
  -> normalized bbox + class
  -> Adapter
  -> DetectionResult[]
```

All three are first-class Pipelines if they satisfy the same boundary contract.

---

## 11. IntermediateArtifact Contract

Internal stages are not standardized, but inspectable outputs may be exposed through generic artifacts.

Conceptual form:

```yaml
artifact_id: artifact-...
stage: detector
artifact_type: detections
scope: recording
path: artifacts/detector/proposals.json
metadata:
  coordinate_space: physical
  units:
    time: s
    frequency: Hz
```

### Recommended generic artifact types

- `spectrogram`
- `iq`
- `spectrum`
- `tensor`
- `detections`
- `image`
- `json`

A Pipeline may define additional artifact types if metadata is sufficient to interpret them.

Artifacts SHOULD specify relevant mapping metadata. Examples:

- spectrogram: time/frequency axes or mapping parameters;
- IQ: sample rate, center frequency, processing/downconversion metadata;
- detections: coordinate system and units.

The UI follows the rule:

> Artifact present -> optionally display it. Artifact absent -> core AnalysisRun remains valid.

The platform MUST NOT require `CPN`, `AHLP`, `FRN`, or any other algorithm-specific stage to exist.

---

## 12. Two Integration Modes

### 12.1 Native Pipeline integration

Use when the algorithm can run in the local platform runtime.

```text
Recording
 -> platform Pipeline engine
 -> Pipeline Adapter
 -> PipelineOutput
 -> AnalysisRun + DetectionResults
```

Expected for CPU-capable or lightweight inference.

### 12.2 External/Imported integration

Use when training/inference occurs on AutoDL or another environment.

```text
External Recording/data
 -> external Pipeline
 -> standard final detections
 -> Analysis Package v1
 -> local Import Existing Run
 -> AnalysisRun + DetectionResults
```

The local platform does not need the external CUDA/PyTorch/training environment.

Both modes MUST converge on the same final AnalysisRun + DetectionResult semantics.

---

## 13. Analysis Package v1 Compatibility

External algorithms MUST deliver results through the existing Analysis Package v1 boundary.

Minimum package:

```text
analysis_package.zip
├── manifest.json
└── detections.json
```

Optional:

```text
metrics.json
artifacts/
```

The package MUST identify:

- schema version;
- pipeline id/name/version;
- LabelSpace;
- recording identity/metadata;
- execution environment;
- detection result path;
- optional metrics/artifacts.

No external Pipeline may depend on a local SQLite primary key being valid on another machine.

---

## 14. Pipeline Versioning and Immutability

A Pipeline is identified by:

```text
pipeline_id + pipeline_version
```

Once used to produce an AnalysisRun, that version's behavior must be reproducible and should be treated as immutable.

### A new Pipeline version is required when a change can materially alter final detections, including:

- model/checkpoint change;
- representation change (STFT -> LS-STFT, resolution, mapping);
- architecture change;
- training recipe change producing new weights;
- confidence threshold/NMS behavior change when treated as part of the named baseline;
- DSP extraction/filter/decimation change;
- classifier/refiner change;
- class mapping change.

Recommended semantic-version interpretation:

- **major:** incompatible task/LabelSpace/output-semantics change;
- **minor:** algorithm/model/representation/training/inference behavior change that is intended to create a new experimental baseline;
- **patch:** packaging or implementation fix with no intended algorithmic behavior change.

Every run must still record exact Git commit/config/checkpoint provenance, because version names alone are insufficient for reproducibility.

Never overwrite an old AnalysisRun to represent a tuned model.

---

## 15. Reproducibility Metadata

For trainable Pipelines, every reproducible baseline SHOULD be able to identify:

```text
pipeline_id
pipeline_version
code git commit
training config
inference config
dataset identity
split manifest
random seed(s)
representation parameters
model architecture
checkpoint path/reference
checkpoint SHA256
validation metrics
execution environment
```

For external imported results, equivalent provenance SHOULD be embedded in the Analysis Package manifest or metadata files.

---

## 16. Training Experiment Manifest

Training is not a local-platform feature, but research code SHOULD emit a lightweight `experiment_manifest.json` or equivalent metadata.

Recommended shape:

```json
{
  "experiment_id": "2026-09-xx-yolo26n-aug-v4",
  "pipeline_id": "spacenet_yolo26n_frn",
  "candidate_version": "1.1.0",
  "git_commit": "...",
  "dataset": "SpaceNet advanced",
  "split_manifest": "...",
  "representation": {
    "type": "ls_stft",
    "config": "..."
  },
  "model": {
    "detector": "yolo26n",
    "classifier": "combined_frn_v3"
  },
  "training_config": "...",
  "seed": 42,
  "checkpoint": "...",
  "checkpoint_sha256": "...",
  "validation_metrics": {}
}
```

This manifest is research provenance, not a required platform database object in v1.

---

## 17. Dataset Split Discipline

To support fair model tuning:

1. Train/validation/test split definitions MUST be frozen and versioned.
2. Hyperparameter/model selection uses train + validation only.
3. Test data is reserved for final/periodic evaluation and must not drive iterative tuning decisions.
4. Any altered split requires a new split identity/manifest and comparison notes.
5. Dataset transforms/augmentation must be recorded in training config.

This prevents test leakage and makes Algorithm Lab comparisons meaningful.

---

## 18. Evaluation Standard

### 18.1 Primary evaluation level

The primary platform evaluation target is **end-to-end final physical output**:

```text
Recording IQ
 -> complete Pipeline
 -> final DetectionResult[]
 -> GroundTruth
 -> metrics
```

Stage-level metrics are diagnostic, not substitutes for end-to-end metrics.

### 18.2 Capability-aware comparison

- `detection_localization` vs `detection_localization`: localization metrics allowed.
- `detection_localization` vs `detection_classification`: compare localization on a common basis; do not fabricate classification metrics for detection-only output.
- `detection_classification` vs `detection_classification`: class-aware metrics are allowed only when LabelSpaces are compatible.

### 18.3 Runtime comparison

Latency/throughput MUST record evaluation conditions such as:

- device;
- batch size;
- input duration/bandwidth;
- representation cache state;
- precision mode;
- software environment.

Latency from different devices/environments must not be presented as directly comparable without qualification.

---

## 19. Multi-stage Pipeline Development Guidance

Multi-stage pipelines introduce error propagation:

```text
detector error
 -> extraction error
 -> classifier/refiner receives degraded input
 -> final error
```

Therefore classifier/refiner development SHOULD distinguish:

### Oracle evaluation

```text
GroundTruth bbox
 -> extraction
 -> classifier/refiner
```

Measures downstream-stage ceiling when localization is perfect.

### Predicted evaluation

```text
Detector prediction
 -> extraction
 -> classifier/refiner
```

Measures real pipeline behavior.

The gap between oracle and predicted evaluation is a useful error-propagation diagnostic.

Downstream models SHOULD avoid training only on perfect GT crops when deployment input comes from imperfect predicted proposals. Candidate strategies include predicted proposals, bbox jitter, and mixed oracle/predicted training, provided the exact recipe is recorded.

---

## 20. Single-stage Pipeline Development Guidance

Single-stage wideband spectrogram detection should explicitly examine:

- very large bandwidth scale differences;
- narrowband small-object detection;
- representation resolution;
- time/frequency pixel mapping;
- class imbalance;
- multi-scale feature assignment;
- augmentation;
- confidence/NMS behavior;
- STFT vs LS-STFT or other frequency warping.

A single-stage detector is an important baseline even if a later multi-stage pipeline performs better.

---

## 21. Training/Optimization Workflow Recommendations

For any new or tuned Pipeline:

1. **Config-driven:** behavior-affecting parameters belong in versioned config rather than scattered code constants.
2. **Smoke first:** validate labels, coordinate inversion, loss behavior, inference output, and package export on a small subset before full training.
3. **Freeze a baseline:** once accepted, preserve config + checkpoint hash + metrics.
4. **Tune as a new version:** never overwrite prior baseline results.
5. **Measure end-to-end:** detector/classifier stage metrics are useful, but final physical metrics decide platform performance.
6. **Preserve failure cases:** store enough outputs to inspect representative FP/FN/localization/classification failures.
7. **Avoid giant derived datasets by default:** prefer on-demand generation or bounded rebuildable caches rather than duplicating raw IQ into large float tensors.

---

## 22. Contribution Contract for New Algorithms

A contributor integrating a new solution must be able to answer five questions:

1. **What is the input?**  
   A platform Recording (native) or externally identified recording/data source (external execution).

2. **What is the final output?**  
   `DetectionResult[]` in seconds + absolute Hz.

3. **What LabelSpace does it use?**  
   A declared stable LabelSpace identity.

4. **How is it executed?**  
   Native Pipeline Adapter or external execution producing Analysis Package v1.

5. **What optional artifacts/provenance are available?**  
   Generic IntermediateArtifacts + run/training metadata.

If these five questions have clear answers, the algorithm is eligible for platform integration regardless of internal architecture.

---

## 23. Acceptance Checklist for a New Pipeline

A Pipeline is platform-compatible only when all applicable items pass:

### Identity

- [ ] Unique stable `pipeline_id`.
- [ ] Explicit `pipeline_version`.
- [ ] Declared task capability.
- [ ] Declared LabelSpace.

### Input

- [ ] Does not require platform core to special-case the algorithm.
- [ ] Native pipeline reads IQ through common Recording access abstraction.
- [ ] External pipeline does not depend on local SQLite IDs.

### Output

- [ ] Final bboxes are seconds + absolute Hz.
- [ ] Bbox bounds validated.
- [ ] Class mapping valid for LabelSpace.
- [ ] Confidence finite and in [0,1].
- [ ] Detection-only pipelines do not fabricate classification.

### Reproducibility

- [ ] Exact code/config can be identified.
- [ ] Checkpoint identity/hash recorded for trained models.
- [ ] Dataset/split identity recorded.
- [ ] Execution environment recorded for external runs.

### Interchange

- [ ] Native Pipeline produces normal AnalysisRun, or
- [ ] External Pipeline produces valid Analysis Package v1.

### Platform UX

- [ ] Results open in Spectrum Analysis.
- [ ] Results appear in Signals.
- [ ] Signal Detail can open final detections.
- [ ] Compatible runs can be evaluated in Algorithm Lab.
- [ ] Missing optional artifacts do not break core result display.

---

## 24. M9 as the First Standard-Conformance Case

The first real external deep-learning integration should validate this standard rather than introduce one-off platform logic.

For the existing historical wideband pipeline, the bridge should:

```text
legacy historical outputs / frozen inference
 -> legacy-specific adapter
 -> validate recording identity
 -> convert coordinates to seconds + absolute Hz
 -> validate LabelSpace
 -> normalize final confidence semantics
 -> attach provenance
 -> Analysis Package v1
 -> local Import Existing Run
 -> normal AnalysisRun
 -> Algorithm Lab
```

The legacy ZoomSpec/Claude/release directories should remain historical assets and should not be modified merely to satisfy the platform. The adapter belongs in the active platform/research repository.

M9 acceptance should prove that a mature heterogeneous external algorithm can integrate without changing the platform's core Recording, AnalysisRun, DetectionResult, Signals, Signal Detail, or Algorithm Lab model.

---

## 25. Standard Evolution Rules

Algorithm Integration Standard v1 should remain stable while M9/M10 and additional contributor pipelines are integrated.

A v2 is justified only if a new algorithm cannot be represented without changing a true boundary contract, e.g. fundamentally different final output semantics.

A new internal stage, new neural architecture, new DSP transform, new checkpoint, or new training method is NOT sufficient reason to change the standard.

The default response to algorithm heterogeneity is:

> **write/change the Adapter, not the platform core contract.**

---

## 26. Summary of Invariants

The following invariants define the platform boundary:

1. Recording is the canonical signal input abstraction.
2. Pipelines may be internally heterogeneous.
3. Architecture family is descriptive only.
4. Final signal localization is always seconds + absolute Hz.
5. Final classification is always tied to an explicit LabelSpace.
6. Detection-only pipelines never fabricate classes.
7. Final confidence is pipeline-defined but bounded to [0,1].
8. AnalysisRun stores the execution result, not the model internals.
9. Optional artifacts expose internals without making them mandatory.
10. External GPU results enter through Analysis Package v1.
11. Training provenance is preserved outside or alongside the platform core.
12. Model tuning creates new reproducible Pipeline versions/runs rather than overwriting history.
13. End-to-end physical metrics are the primary performance truth.
14. Platform core remains stable as algorithms evolve.


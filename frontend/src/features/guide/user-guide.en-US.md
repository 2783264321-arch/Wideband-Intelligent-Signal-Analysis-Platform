# User Guide

This guide explains the main WISA task workflows. It is written for users, not
for developers or API consumers.

## Analyze one IQ file

Open **Data Library**, pick a standalone sample, and choose **Analyze**. Select a
pipeline, leave the execution environment at **Auto**, and select **Run**. The
spectrogram workspace shows detections and Ground Truth when available.

## Analyze a dataset

Open **Data Library**, open a dataset, and review its samples. Choose
**Create Dataset Experiment** to run a pipeline across the dataset. Dataset
identity is prefilled from the dataset you opened.

## Compare two pipelines

Open a sample's **Detections**, select two completed runs, and choose
**Compare**. Algorithm Lab opens with the two runs selected so you can inspect
detection differences.

## Import detection results generated elsewhere

Choose **Import Detection Results** and pick an analysis package. The platform
matches it against your local dataset and samples automatically and never reruns
inference. Imported results appear in the relevant **Detections** list.

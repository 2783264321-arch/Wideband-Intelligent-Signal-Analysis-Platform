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

Open a sample's **Analysis History**, select two completed runs, and choose
**Compare**. Algorithm Lab opens with the two runs selected so you can inspect
detection differences.

## Import server-generated analysis results

Choose **Import Analysis Results** to bring in results produced elsewhere. Use
**Single-sample Analysis Result** for one file and **Dataset Batch Analysis
Result** for a batch package. Imported results appear in the relevant Analysis
History.

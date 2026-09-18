import { Button, Card, Descriptions, List, Space, Tabs, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  deleteBlockersFromError,
  deleteRecording,
  getRecording,
  listAnalysisRuns,
} from "../api/client";
import { useLocalization } from "../localization/useLocalization";
import type { AnalysisRun, DeleteBlocker, RecordingDetail } from "../api/types";
import { DeleteConfirmModal } from "../features/data-library/DeleteConfirmModal";
import { DeleteConflictAlert } from "../features/data-library/DeleteConflictAlert";
import { CompareShortcut } from "../features/algorithm-lab/CompareShortcut";
import { TimeDomainView } from "../features/representations/TimeDomainView";
import { SpectrumView } from "../features/representations/SpectrumView";
import { SampleSpectrogramView } from "../features/representations/SampleSpectrogramView";

/**
 * One product workspace for BOTH Dataset samples and Standalone samples.
 * Dataset membership comes from ``recording.datasetId``. Representations are
 * lazy: each tab fetches only when first activated and caches for the session.
 *
 * Tabs follow the user's mental model: 基本信息 (what is this sample) →
 * 可视化 (what does it look like) → 检测记录 (what did we find, and from where).
 */
export function SampleDetailPage() {
  const { recordingId = "" } = useParams();
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [recording, setRecording] = useState<RecordingDetail | null>(null);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [blockers, setBlockers] = useState<DeleteBlocker[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    void getRecording(recordingId)
      .then(setRecording)
      .catch(() => setRecording(null));
    void listAnalysisRuns(recordingId, null)
      .then(setRuns)
      .catch(() => setRuns([]));
  }, [recordingId]);

  const remove = async () => {
    setDeleting(true);
    setBlockers([]);
    try {
      await deleteRecording(recordingId);
      navigate("/data-library");
    } catch (reason) {
      setBlockers(deleteBlockersFromError(reason));
      setConfirmOpen(false);
    } finally {
      setDeleting(false);
    }
  };

  const isDatasetMember = recording?.datasetId != null;

  const info = recording ? (
    <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
      <Descriptions
        column={1}
        data-testid="sample-overview"
        style={{ minWidth: 260 }}
        items={[
          {
            key: "shape",
            label: t("samples.dataShape"),
            children: `${recording.numSamples.toLocaleString()} 样本`,
          },
          { key: "duration", label: t("samples.duration"), children: `${recording.durationS.toFixed(6)} s` },
          { key: "format", label: t("samples.format"), children: recording.dataFormat },
        ]}
      />
      <Descriptions
        column={1}
        data-testid="sample-info-right"
        items={[
          {
            key: "range",
            label: t("dataLibrary.frequencyRange"),
            children: `${(recording.frequencyLowHz / 1e6).toFixed(3)}–${(recording.frequencyHighHz / 1e6).toFixed(3)} MHz`,
          },
          { key: "fs", label: "Fs", children: `${(recording.sampleRateHz / 1e6).toFixed(3)} MHz` },
          { key: "fc", label: "Fc", children: `${(recording.centerFrequencyHz / 1e9).toFixed(6)} GHz` },
        ]}
      />
    </div>
  ) : (
    <Typography.Text>{t("common.loading")}</Typography.Text>
  );

  const detections = (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <List
        dataSource={runs}
        locale={{ emptyText: t("dataLibrary.empty") }}
        renderItem={(run) => (
          <List.Item
            data-testid="run-history-item"
            actions={[
              run.status === "completed" ? (
                <Button key="view" type="link" onClick={() => navigate(`/spectrum/${recordingId}?run=${run.id}`)}>
                  {t("dataLibrary.viewResults")}
                </Button>
              ) : null,
            ]}
          >
            <Space wrap size={12}>
              <span data-testid="run-history-time">
                {run.startedAt ? new Date(run.startedAt).toLocaleString() : run.createdAt ? new Date(run.createdAt).toLocaleString() : "—"}
              </span>
              <Tag data-testid="run-history-pipeline">{run.pipelineId}</Tag>
              {run.executor === "imported" ? (
                <Tag color="purple" data-testid="run-history-source">
                  {t("samples.sourceImported")}
                </Tag>
              ) : (
                <Tag color="green" data-testid="run-history-source">
                  {t("samples.sourceLocal")}
                </Tag>
              )}
              {run.status !== "completed" ? (
                <Tag>{run.status}</Tag>
              ) : null}
            </Space>
          </List.Item>
        )}
      />
      <Card title={t("common.compare")}>
        <CompareShortcut
          recordingId={recordingId}
          hasGroundTruth={recording?.hasGroundTruth ?? false}
          runs={runs}
          onCompare={(runA, runB) =>
            navigate(`/algorithm-lab?recording=${recordingId}&runA=${runA}&runB=${runB}`)
          }
        />
      </Card>
    </Space>
  );

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            {recording ? recording.name : t("common.loadingRecording")}
          </Typography.Title>
          {recording ? (
            <Space wrap data-testid="sample-context">
              {isDatasetMember ? (
                <>
                  <Tag color="geekblue">{t("dataLibrary.datasetSample")}</Tag>
                  {recording.datasetName ? (
                    <Tag>
                      {recording.datasetName}
                      {recording.datasetSplit ? ` · ${recording.datasetSplit}` : ""}
                    </Tag>
                  ) : null}
                  <Button
                    type="link"
                    style={{ paddingLeft: 0 }}
                    onClick={() => navigate(`/data-library/datasets/${recording.datasetId}`)}
                  >
                    {t("dataLibrary.openDataset")}
                  </Button>
                </>
              ) : (
                <Tag>{t("dataLibrary.standaloneSample")}</Tag>
              )}
            </Space>
          ) : null}
        </div>
        <Space>
          <Button type="primary" onClick={() => navigate(`/spectrum/${recordingId}`)}>
            {t("dataLibrary.analyze")}
          </Button>
          {recording && !isDatasetMember ? (
            <Button
              danger
              onClick={() => {
                setBlockers([]);
                setConfirmOpen(true);
              }}
            >
              {t("dataLibrary.delete")}
            </Button>
          ) : null}
        </Space>
      </div>

      <DeleteConflictAlert blockers={blockers} />

      <Tabs
        items={[
          { key: "info", label: t("samples.tabInfo"), children: info },
          {
            key: "visualize",
            label: t("samples.tabVisualize"),
            children: recording ? (
              <Tabs
                type="card"
                items={[
                  {
                    key: "time",
                    label: t("representation.timeDomain"),
                    children: <TimeDomainView recordingId={recordingId} durationS={recording.durationS} />,
                  },
                  {
                    key: "spectrum",
                    label: t("representation.spectrum"),
                    children: <SpectrumView recordingId={recordingId} />,
                  },
                  {
                    key: "spectrogram",
                    label: t("representation.spectrogram"),
                    children: <SampleSpectrogramView recordingId={recordingId} />,
                  },
                ]}
              />
            ) : null,
          },
          { key: "detections", label: t("samples.tabDetections"), children: detections },
        ]}
      />

      <DeleteConfirmModal
        open={confirmOpen}
        title={t("dataLibrary.delete")}
        body={t("dataLibrary.deleteSampleConfirm")}
        confirmLabel={t("dataLibrary.delete")}
        loading={deleting}
        onConfirm={() => void remove()}
        onCancel={() => setConfirmOpen(false)}
      />
    </Space>
  );
}

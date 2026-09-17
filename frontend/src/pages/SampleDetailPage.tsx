import { Button, Card, Descriptions, List, Space, Tag, Typography } from "antd";
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

/**
 * One product page for BOTH Dataset samples and Standalone samples.
 * Dataset membership comes from ``recording.datasetId``.
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

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ marginBottom: 0 }}>
        {recording ? recording.name : t("common.loadingRecording")}
      </Typography.Title>

      {recording ? (
        <Descriptions
          column={2}
          data-testid="sample-overview"
          items={[
            { key: "fs", label: "Fs", children: `${(recording.sampleRateHz / 1e6).toFixed(3)} MHz` },
            {
              key: "fc",
              label: "Fc",
              children: `${(recording.centerFrequencyHz / 1e9).toFixed(6)} GHz`,
            },
            {
              key: "range",
              label: t("dataLibrary.frequencyRange"),
              children: `${(recording.frequencyLowHz / 1e6).toFixed(3)}–${(recording.frequencyHighHz / 1e6).toFixed(3)} MHz`,
            },
            { key: "duration", label: "Duration", children: `${recording.durationS.toFixed(6)} s` },
            { key: "format", label: "Format", children: recording.dataFormat },
            {
              key: "gt",
              label: t("common.groundTruth"),
              children: recording.hasGroundTruth ? "✓" : "—",
            },
            {
              key: "count",
              label: t("dataLibrary.analysisHistory"),
              children: runs.length,
            },
          ]}
        />
      ) : null}

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

      <DeleteConflictAlert blockers={blockers} />

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

      <Card title={t("dataLibrary.analysisHistory")}>
        <List
          dataSource={runs}
          locale={{ emptyText: t("dataLibrary.empty") }}
          renderItem={(run) => (
            <List.Item
              data-testid="run-history-item"
              actions={[
                <Button key="view" type="link" onClick={() => navigate(`/signals/${run.id}`)}>
                  {t("dataLibrary.viewResults")}
                </Button>,
              ]}
            >
              <Space wrap>
                <Tag>{run.pipelineId}</Tag>
                <Tag>{run.status}</Tag>
                <span>{run.id}</span>
              </Space>
            </List.Item>
          )}
        />
      </Card>

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

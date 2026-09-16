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

export function StandaloneSampleDetailPage() {
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

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ marginBottom: 0 }}>
        {recording ? recording.name : t("common.loadingRecording")}
      </Typography.Title>
      {recording ? (
        <Descriptions
          column={2}
          items={[
            { key: "fs", label: "Fs", children: `${(recording.sampleRateHz / 1e6).toFixed(3)} MHz` },
            {
              key: "fc",
              label: "Fc",
              children: `${(recording.centerFrequencyHz / 1e9).toFixed(6)} GHz`,
            },
            {
              key: "range",
              label: t("dataLibrary.source"),
              children: `${(recording.frequencyLowHz / 1e6).toFixed(3)}–${(recording.frequencyHighHz / 1e6).toFixed(3)} MHz`,
            },
            {
              key: "duration",
              label: "Duration",
              children: `${recording.durationS.toFixed(6)} s`,
            },
            { key: "format", label: "Format", children: recording.dataFormat },
            { key: "source", label: t("dataLibrary.source"), children: recording.source },
          ]}
        />
      ) : null}

      <DeleteConflictAlert blockers={blockers} />

      <Space>
        <Button type="primary" onClick={() => navigate(`/spectrum/${recordingId}`)}>
          {t("dataLibrary.openWorkspace")}
        </Button>
        <Button danger onClick={() => { setBlockers([]); setConfirmOpen(true); }}>
          {t("dataLibrary.delete")}
        </Button>
      </Space>

      <Card title={t("dataLibrary.analysisHistory")}>
        <List
          dataSource={runs}
          locale={{ emptyText: t("dataLibrary.empty") }}
          renderItem={(run) => (
            <List.Item data-testid="run-history-item">
              <Space wrap>
                <Tag>{run.pipelineId}</Tag>
                <Tag>{run.status}</Tag>
                <Tag>{run.executor}</Tag>
                <span>{run.id}</span>
              </Space>
            </List.Item>
          )}
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

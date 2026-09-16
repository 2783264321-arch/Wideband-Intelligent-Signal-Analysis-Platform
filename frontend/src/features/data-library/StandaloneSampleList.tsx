import { Button, Card, Empty, Input, Pagination, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteBlockersFromError, deleteRecording, listStandaloneSamples } from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";
import type { DeleteBlocker, StandaloneSample } from "../../api/types";
import { DeleteConfirmModal } from "./DeleteConfirmModal";
import { DeleteConflictAlert } from "./DeleteConflictAlert";

const PAGE_SIZE = 20;

export function StandaloneSampleList() {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [items, setItems] = useState<StandaloneSample[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<StandaloneSample | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [blockers, setBlockers] = useState<DeleteBlocker[]>([]);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listStandaloneSamples({
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        search: search || undefined,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (reason) {
      setError(toErrorText(reason, t("common.noData")));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, [page, search]);

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    setBlockers([]);
    try {
      await deleteRecording(pendingDelete.id);
      setPendingDelete(null);
      await refresh();
    } catch (reason) {
      const found = deleteBlockersFromError(reason);
      setBlockers(found);
      if (found.length === 0) setError(toErrorText(reason, t("common.noData")));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Input.Search
        allowClear
        placeholder={t("dataLibrary.tabStandalone")}
        onSearch={(value) => {
          setPage(1);
          setSearch(value);
        }}
        style={{ maxWidth: 320 }}
      />
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      <DeleteConflictAlert blockers={blockers} />
      {!loading && items.length === 0 ? <Empty description={t("dataLibrary.empty")} /> : null}
      {items.map((sample) => (
        <Card
          key={sample.id}
          data-testid="standalone-card"
          title={sample.name}
          extra={<Tag>{sample.source}</Tag>}
          actions={[
            <Button key="open" type="link" onClick={() => navigate(`/spectrum/${sample.id}`)}>
              {t("dataLibrary.analyze")}
            </Button>,
            <Button
              key="history"
              type="link"
              onClick={() => navigate(`/data-library/samples/${sample.id}`)}
            >
              {t("dataLibrary.analysisHistory")}
            </Button>,
            <Button
              key="delete"
              type="link"
              danger
              onClick={() => {
                setBlockers([]);
                setPendingDelete(sample);
              }}
            >
              {t("dataLibrary.delete")}
            </Button>,
          ]}
        >
          <Space wrap>
            <Tag>Fs {(sample.sampleRateHz / 1e6).toFixed(3)} MHz</Tag>
            <Tag>Fc {(sample.centerFrequencyHz / 1e9).toFixed(6)} GHz</Tag>
            <Tag>{sample.durationS.toFixed(6)} s</Tag>
            <Tag>{sample.dataFormat}</Tag>
          </Space>
        </Card>
      ))}
      {total > PAGE_SIZE ? (
        <Pagination current={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
      ) : null}
      <DeleteConfirmModal
        open={pendingDelete !== null}
        title={t("dataLibrary.delete")}
        body={t("dataLibrary.deleteSampleConfirm")}
        confirmLabel={t("dataLibrary.delete")}
        loading={deleting}
        onConfirm={() => void confirmDelete()}
        onCancel={() => {
          setPendingDelete(null);
          setBlockers([]);
        }}
      />
    </Space>
  );
}

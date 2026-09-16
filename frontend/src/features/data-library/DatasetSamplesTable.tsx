import { Button, Input, Space, Table } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listDatasetSamples } from "../../api/client";
import { useLocalization } from "../../localization/useLocalization";
import type { DatasetSample } from "../../api/types";

const PAGE_SIZE = 20;

export function DatasetSamplesTable({ datasetProjectionId }: { datasetProjectionId: string }) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [items, setItems] = useState<DatasetSample[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listDatasetSamples(datasetProjectionId, {
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
      search: search || undefined,
    })
      .then((result) => {
        if (!active) return;
        setItems(result.items);
        setTotal(result.total);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [datasetProjectionId, page, search]);

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Input.Search
        allowClear
        style={{ maxWidth: 320 }}
        onSearch={(value) => {
          setPage(1);
          setSearch(value);
        }}
      />
      <Table<DatasetSample>
        rowKey="id"
        dataSource={items}
        loading={loading}
        pagination={{ current: page, pageSize: PAGE_SIZE, total, onChange: setPage }}
        columns={[
          { title: t("recordings.fieldName"), dataIndex: "name", key: "name" },
          {
            title: t("dataLibrary.samples"),
            key: "range",
            render: (_value, record) =>
              `${(record.frequencyLowHz / 1e6).toFixed(3)}–${(record.frequencyHighHz / 1e6).toFixed(3)} MHz` +
              (record.sampleRateDerived ? ` · ${t("dataLibrary.derived")}` : ""),
          },
          {
            title: "Duration",
            key: "duration",
            render: (_value, record) => `${record.durationS.toFixed(6)} s`,
          },
          {
            title: t("common.groundTruth"),
            key: "gt",
            render: (_value, record) => (record.hasGroundTruth ? "✓" : "—"),
          },
          {
            title: t("dataLibrary.analysisHistory"),
            key: "count",
            render: (_value, record) => String(record.analysisCount),
          },
          {
            title: "",
            key: "actions",
            render: (_value, record) => (
              <Button type="link" onClick={() => navigate(`/spectrum/${record.id}`)}>
                {t("spectrum.open")}
              </Button>
            ),
          },
        ]}
      />
    </Space>
  );
}

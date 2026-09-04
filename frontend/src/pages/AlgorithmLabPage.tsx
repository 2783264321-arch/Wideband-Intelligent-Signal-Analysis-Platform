import { Card, Col, Row, Select, Space, Typography } from "antd";

export function AlgorithmLabPage() {
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Typography.Title level={2} style={{ marginBottom: 4 }}>Algorithm Lab</Typography.Title>
        <Typography.Text type="secondary">Compare registered analysis pipelines and inspect controlled processing stages.</Typography.Text>
      </div>
      <Card title="Experiment Setup">
        <Space wrap>
          <Select defaultValue="mock-run" style={{ width: 220 }} options={[{ value: "mock-run", label: "Mock Analysis Run" }]} />
          <Select defaultValue="dummy" style={{ width: 220 }} options={[{ value: "dummy", label: "Dummy Pipeline" }, { value: "zoomspec", label: "ZoomSpec" }]} />
        </Space>
      </Card>
      <Row gutter={16}>
        <Col span={12}><Card title="Overall Performance">Metrics appear after real AnalysisRuns are available.</Card></Col>
        <Col span={12}><Card title="Processing Inspector">Pipeline stages are read-only and pipeline-defined.</Card></Col>
      </Row>
    </Space>
  );
}

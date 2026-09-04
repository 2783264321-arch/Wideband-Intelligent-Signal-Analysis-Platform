import { Button, Card, Space, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { demoRecording } from "../mocks/demo";

export function RecordingsPage() {
  const navigate = useNavigate();
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Typography.Title level={2} style={{ marginBottom: 4 }}>Recording Library</Typography.Title>
        <Typography.Text type="secondary">Open an offline IQ recording or import your own data.</Typography.Text>
      </div>
      <Card
        title={demoRecording.name}
        extra={<Tag>{demoRecording.datasetName}</Tag>}
        actions={[
          <Button key="open" type="link" onClick={() => navigate(`/spectrum/${demoRecording.id}`)}>Start Analysis</Button>,
        ]}
      >
        <Space wrap>
          <Tag>Fs {(demoRecording.sampleRateHz / 1e6).toFixed(1)} MHz</Tag>
          <Tag>Fc {(demoRecording.centerFrequencyHz / 1e9).toFixed(4)} GHz</Tag>
          <Tag>{demoRecording.durationS.toFixed(3)} s</Tag>
          {demoRecording.hasGroundTruth ? <Tag>Ground Truth</Tag> : null}
        </Space>
      </Card>
    </Space>
  );
}

import { Space } from "antd";
import { PageHeader } from "../app/PageHeader";
import { UserGuideContent } from "../features/guide/UserGuideContent";

export function UserGuidePage() {
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader titleKey="guide.title" subtitleKey="guide.subtitle" />
      <UserGuideContent />
    </Space>
  );
}

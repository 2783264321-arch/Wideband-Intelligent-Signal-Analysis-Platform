import { ExperimentOutlined, FolderOpenOutlined, RadarChartOutlined } from "@ant-design/icons";
import { Layout, Menu, Radio, Space, Typography } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useLocalization } from "../localization/useLocalization";
import type { Locale } from "../localization/types";

const { Header, Sider, Content } = Layout;

export function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { locale, setLocale, t } = useLocalization();
  const selected = location.pathname.startsWith("/experiments")
    ? "experiments"
    : location.pathname.startsWith("/algorithm-lab")
      ? "algorithm"
      : "recordings";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider width={220} theme="light" style={{ borderRight: "1px solid #f0f0f0" }}>
        <div style={{ padding: "22px 18px 14px" }}>
          <Typography.Text strong>{t("app.title")}</Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          onClick={({ key }) => {
            const paths: Record<string, string> = { recordings: "/recordings", experiments: "/experiments", algorithm: "/algorithm-lab" };
            navigate(paths[key]);
          }}
          items={[
            { key: "recordings", icon: <FolderOpenOutlined />, label: t("nav.recordings") },
            { key: "experiments", icon: <ExperimentOutlined />, label: t("nav.experiments") },
            { key: "algorithm", icon: <RadarChartOutlined />, label: t("nav.algorithmLab") },
          ]}
        />
      </Sider>
      <Layout>
        <Header style={{ background: "#fff", borderBottom: "1px solid #f0f0f0", paddingInline: 24, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <Typography.Text type="secondary">{t("app.title")} · V1</Typography.Text>
          <Space size={8} align="center">
            <Typography.Text type="secondary">{t("language.switchLabel")}</Typography.Text>
            <Radio.Group
              size="small"
              optionType="button"
              buttonStyle="solid"
              aria-label={t("language.switchLabel")}
              value={locale}
              onChange={(event) => setLocale(event.target.value as Locale)}
            >
              <Radio.Button value="zh-CN">{t("language.zh")}</Radio.Button>
              <Radio.Button value="en-US">{t("language.en")}</Radio.Button>
            </Radio.Group>
          </Space>
        </Header>
        <Content style={{ margin: 20, padding: 24, background: "#fff", borderRadius: 10 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

import { ExperimentOutlined, FolderOpenOutlined, RadarChartOutlined, SettingOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { Layout, Menu, Typography } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

const { Header, Sider, Content } = Layout;

export function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const selected = location.pathname.startsWith("/spectrum") ? "spectrum" : location.pathname.startsWith("/signals") ? "signals" : location.pathname.startsWith("/algorithm-lab") ? "algorithm" : location.pathname.startsWith("/settings") ? "settings" : "recordings";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider width={220} theme="light" style={{ borderRight: "1px solid #f0f0f0" }}>
        <div style={{ padding: "22px 18px 14px" }}>
          <Typography.Text strong>Wideband Signal Lab</Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          onClick={({ key }) => {
            const paths: Record<string, string> = { recordings: "/recordings", spectrum: "/spectrum/rec_demo", signals: "/signals/mock-run", algorithm: "/algorithm-lab", settings: "/settings" };
            navigate(paths[key]);
          }}
          items={[
            { key: "recordings", icon: <FolderOpenOutlined />, label: "Recordings" },
            { key: "spectrum", icon: <RadarChartOutlined />, label: "Spectrum Analysis" },
            { key: "signals", icon: <UnorderedListOutlined />, label: "Signals" },
            { key: "algorithm", icon: <ExperimentOutlined />, label: "Algorithm Lab" },
            { key: "settings", icon: <SettingOutlined />, label: "Settings" },
          ]}
        />
      </Sider>
      <Layout>
        <Header style={{ background: "#fff", borderBottom: "1px solid #f0f0f0", paddingInline: 24 }}>
          <Typography.Text type="secondary">Offline Wideband Intelligent Signal Analysis Platform · V1</Typography.Text>
        </Header>
        <Content style={{ margin: 20, padding: 24, background: "#fff", borderRadius: 10 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

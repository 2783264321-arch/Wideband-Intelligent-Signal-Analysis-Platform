import {
  ExperimentOutlined,
  FolderOpenOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  QuestionCircleOutlined,
  RadarChartOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { Button, Layout, Menu, Radio, Segmented, Space, Typography, theme } from "antd";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useLocalization } from "../localization/useLocalization";
import type { Locale } from "../localization/types";
import { useTheme } from "../theme/useTheme";
import type { ThemePreference } from "../theme/types";
import { sectionTitleKey } from "./sectionTitle";
import { useSidebarCollapsed } from "./useSidebarCollapsed";
import { readAlgorithmLabRoute } from "./workspaceMemory";

const { Header, Sider, Content } = Layout;

const NAV_PATHS: Record<string, string> = {
  dataLibrary: "/data-library",
  experiments: "/experiments",
  algorithm: "/algorithm-lab",
  guide: "/guide",
  settings: "/settings",
};

function selectedKey(pathname: string): string {
  if (pathname.startsWith("/data-library")) return "dataLibrary";
  if (pathname.startsWith("/experiments")) return "experiments";
  if (pathname.startsWith("/algorithm-lab")) return "algorithm";
  if (pathname.startsWith("/guide")) return "guide";
  if (pathname.startsWith("/settings")) return "settings";
  return "dataLibrary";
}

export function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { locale, setLocale, t } = useLocalization();
  const { preference, resolved, setPreference } = useTheme();
  const { collapsed, toggle } = useSidebarCollapsed();
  const { token } = theme.useToken();

  const onMenuClick = (key: string) => {
    if (key === "algorithm") {
      const target =
        location.pathname === "/algorithm-lab" && location.search
          ? "/algorithm-lab"
          : readAlgorithmLabRoute() ?? "/algorithm-lab";
      navigate(target);
      return;
    }
    navigate(NAV_PATHS[key]);
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        data-testid="sidebar"
        data-collapsed={collapsed ? "true" : "false"}
        collapsible
        collapsed={collapsed}
        trigger={null}
        width={220}
        collapsedWidth={64}
        theme={resolved === "dark" ? "dark" : "light"}
        style={{
          background: token.colorBgContainer,
          borderRight: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        {!collapsed ? (
          <div style={{ padding: "22px 18px 14px" }}>
            <Typography.Text strong>{t("app.title")}</Typography.Text>
          </div>
        ) : null}
        <Menu
          mode="inline"
          theme={resolved === "dark" ? "dark" : "light"}
          selectedKeys={[selectedKey(location.pathname)]}
          onClick={({ key }) => onMenuClick(key)}
          items={[
            { key: "dataLibrary", icon: <FolderOpenOutlined />, label: t("nav.dataLibrary") },
            { key: "experiments", icon: <ExperimentOutlined />, label: t("nav.experiments") },
            { key: "algorithm", icon: <RadarChartOutlined />, label: t("nav.algorithmLab") },
            { key: "guide", icon: <QuestionCircleOutlined />, label: t("nav.guide") },
            { key: "settings", icon: <SettingOutlined />, label: t("nav.settings") },
          ]}
        />
        <div style={{ padding: 8 }}>
          <Button
            data-testid="sidebar-toggle"
            type="text"
            aria-label={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={toggle}
          />
        </div>
      </Sider>
      <Layout>
        <Header
          data-testid="header"
          style={{
            background: token.colorBgContainer,
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
            paddingInline: 24,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography.Text data-testid="header-context">
            {t(sectionTitleKey(location.pathname))}
          </Typography.Text>
          <Space size={12} align="center">
            <Segmented
              data-testid="header-theme"
              size="small"
              value={preference}
              onChange={(value) => setPreference(value as ThemePreference)}
              options={[
                { label: t("theme.system"), value: "system" },
                { label: t("theme.light"), value: "light" },
                { label: t("theme.dark"), value: "dark" },
              ]}
            />
            <Typography.Text type="secondary">{t("language.switchLabel")}</Typography.Text>
            <Radio.Group
              data-testid="header-language"
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
            <Link data-testid="header-guide" to="/guide" aria-label={t("guide.open")}>
              <QuestionCircleOutlined />
            </Link>
          </Space>
        </Header>
        <Content
          style={{
            margin: 20,
            padding: 24,
            background: token.colorBgContainer,
            borderRadius: 10,
          }}
        >
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

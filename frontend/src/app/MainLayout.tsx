import {
  ApartmentOutlined,
  ExperimentOutlined,
  FolderOpenOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  QuestionCircleOutlined,
  RadarChartOutlined,
  SettingOutlined,
  SunOutlined,
  TranslationOutlined,
} from "@ant-design/icons";
import { Button, Layout, Menu, Space, Tooltip, Typography, theme } from "antd";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useLocalization } from "../localization/useLocalization";
import type { Locale } from "../localization/types";
import { useTheme } from "../theme/useTheme";
import type { ThemePreference } from "../theme/types";
import { sectionTitleKey } from "./sectionTitle";
import { useSidebarCollapsed } from "./useSidebarCollapsed";
import { readAlgorithmLabRoute } from "./workspaceMemory";

const { Header, Sider, Content } = Layout;

function themeButtonStyle(active: boolean, token: ReturnType<typeof theme.useToken>["token"]) {
  return active
    ? { background: token.colorPrimaryBg, color: token.colorPrimary }
    : { color: token.colorTextSecondary };
}

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
        {/*
          Brand region is ALWAYS mounted at a fixed height so collapse is a purely
          horizontal transition: the menu top and all vertical positions stay put.
          Only the brand text visibility (opacity/max-width) changes.
        */}
        <div
          data-testid="sidebar-brand"
          style={{
            height: 64,
            display: "flex",
            alignItems: "center",
            gap: 10,
            paddingInline: 22,
            overflow: "hidden",
            flexShrink: 0,
            whiteSpace: "nowrap",
          }}
        >
          <span data-testid="sidebar-brand-icon" style={{ display: "inline-flex", flexShrink: 0 }}>
            <ApartmentOutlined style={{ fontSize: 20 }} />
          </span>
          <Typography.Text
            strong
            data-testid="sidebar-brand-title"
            style={{
              display: "inline-block",
              maxWidth: collapsed ? 0 : 160,
              opacity: collapsed ? 0 : 1,
              overflow: "hidden",
              whiteSpace: "nowrap",
              transition: "opacity 200ms ease, max-width 200ms ease",
            }}
          >
            {t("app.title")}
          </Typography.Text>
        </div>
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
        <div
          style={{
            padding: "8px 14px",
            // Align the fold/expand control with the 14px menu glyph column
            // (antd menu icon box) plus the sider's 22px padding.
            paddingInlineStart: 22,
            marginTop: 8,
          }}
        >
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
          <Space size={4} align="center">
            <Tooltip title={t("theme.system")}>
              <Button
                data-testid="header-theme-system"
                type="text"
                size="small"
                aria-label={t("theme.system")}
                icon={
                  <span style={{ fontSize: 11, lineHeight: "22px" }}>
                    {t("theme.systemIcon")}
                  </span>
                }
                onClick={() => setPreference("system")}
                style={themeButtonStyle(preference === "system", token)}
              />
            </Tooltip>
            <Tooltip title={t("theme.light")}>
              <Button
                data-testid="header-theme-light"
                type="text"
                size="small"
                aria-label={t("theme.light")}
                icon={<SunOutlined style={{ color: token.colorWarning }} />}
                onClick={() => setPreference("light")}
                style={themeButtonStyle(preference === "light", token)}
              />
            </Tooltip>
            <Tooltip title={t("theme.dark")}>
              <Button
                data-testid="header-theme-dark"
                type="text"
                size="small"
                aria-label={t("theme.dark")}
                icon={<MoonOutlined />}
                onClick={() => setPreference("dark")}
                style={themeButtonStyle(preference === "dark", token)}
              />
            </Tooltip>
            <span
              style={{ width: 1, height: 16, background: token.colorBorder, display: "inline-block" }}
            />
            <Tooltip title={t("language.switchLabel")}>
              <Button
                data-testid="header-language-zh"
                type="text"
                size="small"
                aria-label={t("language.zh")}
                icon={<span style={{ fontWeight: 600 }}>中</span>}
                onClick={() => setLocale("zh-CN")}
                style={themeButtonStyle(locale === "zh-CN", token)}
              />
            </Tooltip>
            <Tooltip title={t("language.en")}>
              <Button
                data-testid="header-language-en"
                type="text"
                size="small"
                aria-label={t("language.en")}
                icon={<span style={{ fontWeight: 600 }}>EN</span>}
                onClick={() => setLocale("en-US")}
                style={themeButtonStyle(locale === "en-US", token)}
              />
            </Tooltip>
            <Tooltip title={t("guide.open")}>
              <Link data-testid="header-guide" to="/guide" aria-label={t("guide.open")}>
                <Button type="text" size="small" icon={<QuestionCircleOutlined />} />
              </Link>
            </Tooltip>
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

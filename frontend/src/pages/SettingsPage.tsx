import { Card, Radio, Segmented, Space, Switch, Typography } from "antd";
import { Link } from "react-router-dom";
import { PageHeader } from "../app/PageHeader";
import { useSidebarCollapsed } from "../app/useSidebarCollapsed";
import { useLocalization } from "../localization/useLocalization";
import { useTheme } from "../theme/useTheme";
import type { Locale } from "../localization/types";
import type { ThemePreference } from "../theme/types";

export function SettingsPage() {
  const { t, locale, setLocale } = useLocalization();
  const { preference, setPreference } = useTheme();
  const { collapsed, setCollapsed } = useSidebarCollapsed();

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <PageHeader titleKey="settings.title" subtitleKey="settings.subtitle" />
      <Card title={t("settings.appearance")}>
        <Space direction="vertical">
          <Typography.Text type="secondary">{t("theme.title")}</Typography.Text>
          <Segmented
            data-testid="settings-theme"
            value={preference}
            onChange={(value) => setPreference(value as ThemePreference)}
            options={[
              { label: t("theme.system"), value: "system" },
              { label: t("theme.light"), value: "light" },
              { label: t("theme.dark"), value: "dark" },
            ]}
          />
        </Space>
      </Card>
      <Card title={t("settings.language")}>
        <Radio.Group
          data-testid="settings-language"
          value={locale}
          onChange={(event) => setLocale(event.target.value as Locale)}
        >
          <Radio.Button value="zh-CN">{t("language.zh")}</Radio.Button>
          <Radio.Button value="en-US">{t("language.en")}</Radio.Button>
        </Radio.Group>
      </Card>
      <Card title={t("settings.sidebar")}>
        <Space>
          <Switch
            data-testid="settings-sidebar"
            checked={collapsed}
            onChange={(checked) => setCollapsed(checked)}
          />
          <Typography.Text>{t("settings.sidebarCollapsed")}</Typography.Text>
        </Space>
      </Card>
      <Link data-testid="settings-open-guide" to="/guide">
        {t("settings.openGuide")}
      </Link>
    </Space>
  );
}

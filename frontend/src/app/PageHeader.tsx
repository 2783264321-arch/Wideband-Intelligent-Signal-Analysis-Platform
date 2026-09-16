import { Typography } from "antd";
import { useLocalization } from "../localization/useLocalization";
import type { MessageKey } from "../localization/types";

export interface PageHeaderProps {
  titleKey: MessageKey;
  subtitleKey?: MessageKey;
}

export function PageHeader({ titleKey, subtitleKey }: PageHeaderProps) {
  const { t } = useLocalization();
  return (
    <div>
      <Typography.Title level={2} style={{ marginBottom: 4 }}>
        {t(titleKey)}
      </Typography.Title>
      {subtitleKey ? <Typography.Text type="secondary">{t(subtitleKey)}</Typography.Text> : null}
    </div>
  );
}

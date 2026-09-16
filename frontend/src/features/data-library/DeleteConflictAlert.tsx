import { Alert } from "antd";
import { useLocalization } from "../../localization/useLocalization";
import type { DeleteBlocker } from "../../api/types";

export interface DeleteConflictAlertProps {
  blockers: DeleteBlocker[];
}

export function DeleteConflictAlert({ blockers }: DeleteConflictAlertProps) {
  const { t } = useLocalization();
  if (blockers.length === 0) return null;
  return (
    <Alert
      type="error"
      showIcon
      data-testid="delete-conflict-alert"
      message={t("dataLibrary.deleteConflictTitle")}
      description={
        <ul>
          {blockers.map((blocker, index) => (
            <li key={`${blocker.kind}-${blocker.resourceId}-${index}`}>
              {blocker.kind}: {blocker.resourceId}
            </li>
          ))}
        </ul>
      }
    />
  );
}

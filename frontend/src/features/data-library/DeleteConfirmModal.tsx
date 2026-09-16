import { Modal, Typography } from "antd";
import { useLocalization } from "../../localization/useLocalization";

export interface DeleteConfirmModalProps {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  loading?: boolean;
}

export function DeleteConfirmModal({
  open,
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
  loading,
}: DeleteConfirmModalProps) {
  const { t } = useLocalization();
  return (
    <Modal
      open={open}
      title={title}
      okText={confirmLabel}
      cancelText={t("common.cancel")}
      okButtonProps={{ danger: true, loading }}
      onOk={onConfirm}
      onCancel={onCancel}
    >
      <Typography.Paragraph>{body}</Typography.Paragraph>
    </Modal>
  );
}

import { Button, Group, Modal, Text } from "@mantine/core";
import type { ReactNode } from "react";

/** A yes/no question before something that cannot be undone (red confirm button). */
export function ConfirmModal({ opened, title, confirm, onCancel, onConfirm, testId, children }: {
  opened: boolean;
  title: string;
  confirm: string;
  onCancel: () => void;
  onConfirm: () => void;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <Modal opened={opened} onClose={onCancel} title={title}>
      <Text size="sm">{children}</Text>
      <Group justify="flex-end" mt="md">
        <Button variant="default" onClick={onCancel}>Cancel</Button>
        <Button color="red" onClick={onConfirm} data-testid={testId}>{confirm}</Button>
      </Group>
    </Modal>
  );
}

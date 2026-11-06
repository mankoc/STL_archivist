// A folder's stored path pattern; editors change it in place, or forget it.
import { ActionIcon, Group, Text, TextInput, Tooltip } from "@mantine/core";
import { IconPencil, IconTrash } from "@tabler/icons-react";
import { useState } from "react";

export function StoredPattern({ folder, pattern, editor, onSave }: { folder: string; pattern: string; editor: boolean; onSave: (pattern: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft !== null) {
    return (
      <TextInput
        size="xs"
        autoFocus
        label={folder}
        value={draft}
        onChange={(e) => setDraft(e.currentTarget.value)}
        onBlur={() => setDraft(null)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setDraft(null);
          if (e.key === "Enter") {
            onSave(draft);
            setDraft(null);
          }
        }}
      />
    );
  }
  return (
    <Group gap={2} wrap="nowrap">
      <Text size="xs" c="dimmed">{folder}: {pattern || "(none)"}</Text>
      {editor && (
        <>
          <Tooltip label="Change this folder's pattern">
            <ActionIcon size="xs" variant="subtle" aria-label={`Change the pattern of ${folder}`} onClick={() => setDraft(pattern)}>
              <IconPencil size={12} />
            </ActionIcon>
          </Tooltip>
          {pattern && (
            <Tooltip label="Forget this folder's pattern">
              <ActionIcon size="xs" variant="subtle" color="red" aria-label={`Forget the pattern of ${folder}`} onClick={() => onSave("")}>
                <IconTrash size={12} />
              </ActionIcon>
            </Tooltip>
          )}
        </>
      )}
    </Group>
  );
}

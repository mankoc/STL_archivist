// A folder's path pattern; editors change it in place (with a sample of what it reads,
// when given the folder), or forget it.
import { ActionIcon, Group, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { IconCheck, IconPencil, IconTrash, IconX } from "@tabler/icons-react";
import { useState } from "react";

import { PatternSample } from "../browser/PatternSample";

export function StoredPattern({ folder, pattern, editor, onSave, fallback, sampleFolder, count }: {
  folder: string;
  pattern: string;
  editor: boolean;
  onSave: (pattern: string) => void;
  /** Shown when it has none: the pattern it uses instead. */
  fallback?: string;
  /** The folder as the filter tree spells it, for the sample while editing. */
  sampleFolder?: string;
  /** Archives directly in it. */
  count?: number;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft !== null) {
    const save = () => {
      onSave(draft);
      setDraft(null);
    };
    return (
      <Stack gap={6} p={6} style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 6 }}>
        <TextInput
          size="xs"
          autoFocus
          label={folder}
          description="Empty: the library folder's pattern"
          value={draft}
          onChange={(e) => setDraft(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setDraft(null);
            if (e.key === "Enter") save();
          }}
          rightSectionWidth={56}
          rightSection={
            <Group gap={2} wrap="nowrap">
              <ActionIcon size="sm" variant="subtle" aria-label="Save the pattern" onClick={save}>
                <IconCheck size={14} />
              </ActionIcon>
              <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Cancel" onClick={() => setDraft(null)}>
                <IconX size={14} />
              </ActionIcon>
            </Group>
          }
        />
        {sampleFolder && <PatternSample folder={sampleFolder} pattern={draft || fallback || ""} />}
      </Stack>
    );
  }
  return (
    <Group gap={4} wrap="nowrap">
      <Text size="xs" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
        {folder}
        {count !== undefined && (
          <Text span size="xs" c="dimmed">
            {" "}
            ({count})
          </Text>
        )}
        {": "}
        {pattern ? (
          <Text span size="xs" ff="monospace">
            {pattern}
          </Text>
        ) : (
          <Text span size="xs" c="dimmed">
            {fallback ? `the library's (${fallback})` : "(none)"}
          </Text>
        )}
      </Text>
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

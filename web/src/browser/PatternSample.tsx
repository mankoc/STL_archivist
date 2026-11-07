// What a path pattern reads from a few archives directly in a folder (picked at
// random), as the crawl would: shown beside the pattern being typed.
import { Button, Code, Group, Stack, Text } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconRefresh } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "../api/client";
import { fieldLabel } from "./patterns";

export function PatternSample({ folder, pattern }: { folder: string; pattern: string }) {
  const [typed] = useDebouncedValue(pattern.trim(), 250);
  const q = useQuery({
    queryKey: ["pattern-sample", folder, typed],
    queryFn: async () => unwrap(await api.GET("/api/libraries/pattern-sample", { params: { query: { folder, pattern: typed } } })),
    placeholderData: (previous) => previous,
    retry: false,
  });
  if (!q.data) return null;
  if (q.data.length === 0) {
    return (
      <Text size="xs" c="dimmed" data-testid="pattern-sample">
        No archives directly in this folder: its sub-folders have patterns of their own (Settings → Patterns).
      </Text>
    );
  }
  return (
    <Stack gap={6} data-testid="pattern-sample">
      <Group justify="space-between" gap="xs">
        <Text size="xs" fw={600}>
          Sample: archives directly in this folder
        </Text>
        <Button size="compact-xs" variant="subtle" leftSection={<IconRefresh size={12} />} onClick={() => void q.refetch()} loading={q.isFetching}>
          Other samples
        </Button>
      </Group>
      {q.data.map((s) => (
        <div key={s.path}>
          <Code block style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>{s.path}</Code>
          <Text size="xs" c={s.fields.length ? undefined : "dimmed"} mt={2}>
            {s.fields.length
              ? s.fields.map((f) => `${fieldLabel(f.field)}: ${f.value || "(empty)"}`).join(" · ")
              : typed
                ? "The pattern doesn't fit this path: nothing is read from it."
                : "No pattern: values come from the archive only."}
          </Text>
        </div>
      ))}
    </Stack>
  );
}

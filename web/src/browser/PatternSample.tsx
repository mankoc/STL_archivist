// What a path pattern reads from a folder's first archives, as the crawl would: shown
// beside the pattern being typed.
import { Code, Stack, Text } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
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
  if (!q.data?.length) return null;
  return (
    <Stack gap={6} data-testid="pattern-sample">
      <Text size="xs" fw={600}>Sample</Text>
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

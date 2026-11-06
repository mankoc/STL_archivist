// Settings → Patterns: the path patterns stored for library folders and for the folders
// inside them (*Crawl with a pattern…*), to change or forget without crawling.
import { Card, Loader, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "../api/client";
import { StoredPattern } from "../jobs/StoredPattern";

export function PatternsTab({ editor }: { editor: boolean }) {
  const queryClient = useQueryClient();
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: async () => unwrap(await api.GET("/api/libraries")) });
  const save = async (id: number, body: { pattern?: string; subfolder?: { folder: string; pattern: string } }) => {
    try {
      unwrap(await api.PATCH("/api/libraries/folders/{id}", { params: { path: { id } }, body }));
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
    await queryClient.invalidateQueries({ queryKey: ["libraries"] });
  };
  if (!libraries.data) return <Loader size="sm" />;
  return (
    <Stack maw={900} data-testid="stored-patterns">
      <Text size="sm" c="dimmed">
        The path patterns crawls, monitoring and Rescan use. A folder inside a library folder has one when it was crawled with
        Crawl with a pattern; forgetting it makes the folder use its library folder's pattern again. A change applies from the
        next crawl or Rescan.
      </Text>
      {libraries.data.map((l) => (
        <Card key={l.id} withBorder>
          <Title order={5} mb="xs">{l.name}</Title>
          <Stack gap={4}>
            {l.folders.filter((f) => f.root).map((f) => (
              <Stack key={f.id} gap={2}>
                <StoredPattern folder={f.root} pattern={f.pattern ?? ""} editor={editor} onSave={(pattern) => void save(f.id, { pattern })} />
                {f.subfolder_patterns.map((s) => (
                  <div key={s.folder} style={{ paddingLeft: 16 }}>
                    <StoredPattern folder={s.folder} pattern={s.pattern} editor={editor} onSave={(pattern) => void save(f.id, { subfolder: { folder: s.folder, pattern } })} />
                  </div>
                ))}
              </Stack>
            ))}
          </Stack>
        </Card>
      ))}
      {libraries.data.length === 0 && <Text size="sm" c="dimmed">No libraries yet.</Text>}
    </Stack>
  );
}

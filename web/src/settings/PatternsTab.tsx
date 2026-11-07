// Settings → Patterns: each library folder's path pattern, and every folder below it
// that holds archives, each with its own pattern or the library folder's. A folder's
// pattern is for the archives directly in it, not those of its sub-folders.
import { Card, Loader, Stack, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { StoredPattern } from "../jobs/StoredPattern";
import { folderRows } from "./folderRows";

type Folder = Schemas["LibraryFolder"];

export function PatternsTab({ editor }: { editor: boolean }) {
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: async () => unwrap(await api.GET("/api/libraries")) });
  if (!libraries.data) return <Loader size="sm" />;
  return (
    <Stack maw={900} data-testid="stored-patterns">
      <Text size="sm" c="dimmed">
        The path patterns crawls, monitoring and Rescan use. A folder's pattern is for the archives directly in it; its
        sub-folders use their own, or the library folder's when they have none. A change applies from the next crawl or
        Rescan. Editing one shows a sample of what it reads.
      </Text>
      {libraries.data.map((l) => (
        <Card key={l.id} withBorder>
          <Title order={5} mb="xs">{l.name}</Title>
          <Stack gap="md">
            {l.folders.filter((f) => f.root).map((f) => (
              <FolderPatterns key={f.id} folder={f} editor={editor} />
            ))}
          </Stack>
        </Card>
      ))}
      {libraries.data.length === 0 && <Text size="sm" c="dimmed">No libraries yet.</Text>}
    </Stack>
  );
}

function FolderPatterns({ folder, editor }: { folder: Folder; editor: boolean }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("");
  const rows = useQuery({
    queryKey: ["folder-patterns", folder.id],
    queryFn: async () => unwrap(await api.GET("/api/libraries/folders/{id}/folders", { params: { path: { id: folder.id } } })),
  });
  const save = async (body: { pattern?: string; subfolder?: { folder: string; pattern: string } }) => {
    try {
      unwrap(await api.PATCH("/api/libraries/folders/{id}", { params: { path: { id: folder.id } }, body }));
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
    await queryClient.invalidateQueries({ queryKey: ["libraries"] });
    await queryClient.invalidateQueries({ queryKey: ["folder-patterns", folder.id] });
  };
  const library = folder.pattern ?? "";
  const shown = folderRows(rows.data ?? [], filter);
  return (
    <Stack gap={4}>
      <StoredPattern folder={folder.root} pattern={library} editor={editor} sampleFolder={folder.root} onSave={(pattern) => void save({ pattern })} />
      {rows.data && rows.data.length > 0 && (
        <Stack gap={4} pl="md">
          {rows.data.length > 10 && (
            <TextInput size="xs" placeholder="Filter folders" value={filter} onChange={(e) => setFilter(e.currentTarget.value)} maw={320} />
          )}
          {shown.rows.map((r) => (
            <StoredPattern
              key={r.folder}
              folder={r.folder}
              count={r.archives}
              pattern={r.pattern ?? ""}
              fallback={library}
              editor={editor}
              sampleFolder={`${folder.root.replace(/[\\/]+$/, "")}/${r.folder}`}
              onSave={(pattern) => void save({ subfolder: { folder: r.folder, pattern } })}
            />
          ))}
          {shown.hidden > 0 && (
            <Text size="xs" c="dimmed">
              {shown.hidden} more: filter to find them.
            </Text>
          )}
        </Stack>
      )}
      {rows.isPending && <Loader size="xs" />}
    </Stack>
  );
}

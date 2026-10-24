// The detail pane's archive section: the stored warnings with the members that
// trigger them (Warning Details), and the files in the archive with their CRC-32
// (Show Files), as the last crawl or rescan read the archive's catalog.
import { Alert, Badge, Button, Group, Loader, Modal, Stack, Table, Text } from "@mantine/core";
import { IconAlertTriangle, IconFiles } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";

export function ArchiveInfo({ id, warning }: { id: number; warning: string | null }) {
  const [open, setOpen] = useState<"warnings" | "files" | null>(null);
  const lines = (warning ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
  return (
    <Stack gap={6}>
      {lines.length > 0 && (
        <Group gap={4}>
          {lines.map((l) => (
            <Badge key={l} color="orange" variant="light" size="sm" tt="none" leftSection={<IconAlertTriangle size={12} />}>
              {l}
            </Badge>
          ))}
        </Group>
      )}
      <Group gap="xs">
        {lines.length > 0 && (
          <Button size="compact-xs" variant="subtle" onClick={() => setOpen("warnings")} data-testid="warning-details">
            Warning details
          </Button>
        )}
        <Button size="compact-xs" variant="subtle" leftSection={<IconFiles size={12} />} onClick={() => setOpen("files")} data-testid="show-files">
          Files
        </Button>
      </Group>
      <Modal opened={open === "warnings"} onClose={() => setOpen(null)} title="Warning details" size="lg">
        {open === "warnings" && <WarningDetails id={id} />}
      </Modal>
      <Modal opened={open === "files"} onClose={() => setOpen(null)} title="Files in the archive" size="lg">
        {open === "files" && <FileList id={id} />}
      </Modal>
    </Stack>
  );
}

function WarningDetails({ id }: { id: number }) {
  const q = useQuery({
    queryKey: ["model-warnings", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/warnings", { params: { path: { id } } })),
  });
  if (q.isPending) return <Loader size="sm" />;
  if (q.isError) return <Alert color="red">{q.error.message}</Alert>;
  return (
    <Stack data-testid="warning-sections">
      {q.data.error && <Alert color="orange">{q.data.error}</Alert>}
      {q.data.sections.map((s) => (
        <div key={s.warning}>
          <Text fw={600} size="sm">{s.warning}</Text>
          {s.members.length ? (
            <Table fz="xs" verticalSpacing={2}>
              <Table.Tbody>
                {s.members.map((m) => (
                  <Table.Tr key={m.name}>
                    <Table.Td style={{ wordBreak: "break-all" }}>{m.name}</Table.Td>
                    <Table.Td w={90} ta="right">{formatSize(m.size)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          ) : (
            <Text size="xs" c="dimmed">
              No file triggers it: it is raised by something missing, or no longer applies.
            </Text>
          )}
        </div>
      ))}
    </Stack>
  );
}

function FileList({ id }: { id: number }) {
  const q = useQuery({
    queryKey: ["model-files", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/archive-files", { params: { path: { id } } })),
  });
  if (q.isPending) return <Loader size="sm" />;
  if (q.isError) return <Alert color="red">{q.error.message}</Alert>;
  if (!q.data.length) {
    return <Text size="sm" c="dimmed">No file list yet: the next crawl or rescan of this archive stores it.</Text>;
  }
  return (
    <Table fz="xs" striped verticalSpacing={2} data-testid="file-list">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Name</Table.Th>
          <Table.Th w={90} ta="right">Size</Table.Th>
          <Table.Th w={80}>CRC-32</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {q.data.map((f, i) => (
          <Table.Tr key={`${i}:${f.name}`}>
            <Table.Td style={{ wordBreak: "break-all" }}>{f.name}</Table.Td>
            <Table.Td w={90} ta="right">{formatSize(f.size)}</Table.Td>
            <Table.Td w={80} ff="monospace">{f.crc}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

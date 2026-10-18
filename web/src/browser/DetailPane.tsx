// The selected model: its fields, thumbnails, and the archive (download, or
// the folder path when this client can open it there).
import { Anchor, Badge, Box, Button, Center, CopyButton, Group, Image, Loader, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { IconCopy, IconDownload } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "../api/client";
import { baseName, formatDate, formatSize } from "../format";

const FIELDS: [string, string][] = [
  ["creator", "Creator"],
  ["source", "Type"],
  ["sculptor_name", "Sculptor"],
  ["collection_name", "Collection"],
  ["subcollection_name", "Subcollection"],
  ["category_name", "Category"],
  ["release_name", "Release"],
];

export function DetailPane({ id }: { id: number | null }) {
  const detail = useQuery({
    queryKey: ["model", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}", { params: { path: { id: id! } } })),
    enabled: id != null,
  });
  if (id == null) {
    return (
      <Center h="100%" p="md">
        <Text c="dimmed" size="sm">
          Select a model to see its details.
        </Text>
      </Center>
    );
  }
  if (detail.isPending) {
    return (
      <Center h="100%">
        <Loader size="sm" />
      </Center>
    );
  }
  if (detail.isError) {
    return (
      <Center h="100%" p="md">
        <Text c="red" size="sm">
          {detail.error.message}
        </Text>
      </Center>
    );
  }
  const d = detail.data;
  const f = d.fields as Record<string, string | number | null>;
  const path = (f.archive_path as string | null) ?? "";
  const tags = String(f.tags ?? "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  return (
    <Stack gap="sm" p="sm" style={{ height: "100%", overflowY: "auto" }}>
      <Title order={4}>{(f.title as string) || baseName(path)}</Title>
      {f.is_missing === 1 && (
        <Badge color="red" variant="light">
          Archive missing
        </Badge>
      )}
      <Table withRowBorders={false} verticalSpacing={2} fz="sm">
        <Table.Tbody>
          {FIELDS.filter(([k]) => f[k]).map(([k, label]) => (
            <Table.Tr key={k}>
              <Table.Td c="dimmed" w={110}>
                {label}
              </Table.Td>
              <Table.Td>{String(f[k])}</Table.Td>
            </Table.Tr>
          ))}
          {f.release_year != null && (
            <Table.Tr>
              <Table.Td c="dimmed">Date</Table.Td>
              <Table.Td>{formatDate(f.release_year as number, f.release_month as number | null)}</Table.Td>
            </Table.Tr>
          )}
          <Table.Tr>
            <Table.Td c="dimmed">Size</Table.Td>
            <Table.Td>{formatSize(f.archive_size as number | null)}</Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>
      {tags.length > 0 && (
        <Group gap={4}>
          {tags.map((t) => (
            <Badge key={t} variant="light" size="sm">
              {t}
            </Badge>
          ))}
        </Group>
      )}
      <Box>
        <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
          {path}
        </Text>
        <Group gap="xs" mt={4}>
          <Button
            component="a"
            href={`/api/models/${id}/archive`}
            download
            size="xs"
            variant="light"
            leftSection={<IconDownload size={14} />}
          >
            Download
          </Button>
          {d.client_path && (
            <CopyButton value={d.client_path}>
              {({ copied, copy }) => (
                <Button size="xs" variant="subtle" onClick={copy} leftSection={<IconCopy size={14} />}>
                  {copied ? "Copied" : "Copy path"}
                </Button>
              )}
            </CopyButton>
          )}
        </Group>
      </Box>
      <SimpleGrid cols={2} spacing="xs">
        {d.images.map((img) =>
          img.thumbnail ? (
            <Anchor key={img.id} href={img.thumbnail} target="_blank">
              <Image src={img.thumbnail} radius="sm" alt="" loading="lazy" />
            </Anchor>
          ) : null,
        )}
      </SimpleGrid>
      {d.images.length === 0 && (
        <Text size="sm" c="dimmed">
          No images.
        </Text>
      )}
    </Stack>
  );
}

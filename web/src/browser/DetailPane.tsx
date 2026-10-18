// The selected model: its fields, thumbnails, and the archive (download, or
// the folder path when this client can open it there).
import { ActionIcon, Anchor, Badge, Box, Button, Center, CopyButton, Group, Image, Loader, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { Dropzone, IMAGE_MIME_TYPE } from "@mantine/dropzone";
import { notifications } from "@mantine/notifications";
import { IconCopy, IconDownload, IconEdit, IconPhotoPlus, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type Schemas, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import { baseName, formatDate, formatSize } from "../format";
import classes from "./DetailPane.module.css";

const FIELDS: [string, string][] = [
  ["creator", "Creator"],
  ["source", "Type"],
  ["sculptor_name", "Sculptor"],
  ["collection_name", "Collection"],
  ["subcollection_name", "Subcollection"],
  ["category_name", "Category"],
  ["release_name", "Release"],
];

/** Upload images to a model (multipart; the server makes the thumbnails). */
async function uploadImages(id: number, files: File[]): Promise<Schemas["AddedImages"]> {
  const form = new FormData();
  for (const f of files) form.append("file", f, f.name);
  const resp = await fetch(`/api/models/${id}/images`, { method: "POST", body: form, credentials: "same-origin" });
  const body = await resp.json();
  if (!resp.ok) throw new ApiError(resp.status, body.error ?? resp.statusText);
  return body;
}

export function DetailPane({ id, onEdit }: { id: number | null; onEdit: (id: number) => void }) {
  const user = useUser();
  const editor = hasRole(user, "editor");
  const queryClient = useQueryClient();
  const refresh = async () => {
    for (const key of ["model", "rows", "editor"]) await queryClient.invalidateQueries({ queryKey: [key] });
  };
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
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Title order={4}>{(f.title as string) || baseName(path)}</Title>
        {editor && (
          <Button size="xs" variant="light" leftSection={<IconEdit size={14} />} onClick={() => onEdit(id)}>
            Edit
          </Button>
        )}
      </Group>
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
            <Box key={img.id} className={classes.thumb}>
              <Anchor href={img.thumbnail} target="_blank">
                <Image src={img.thumbnail} radius="sm" alt="" loading="lazy" />
              </Anchor>
              {editor && (
                <ActionIcon
                  className={classes.remove}
                  color="red"
                  variant="filled"
                  size="sm"
                  aria-label="Remove image"
                  onClick={async () => {
                    try {
                      unwrap(await api.DELETE("/api/images/{id}", { params: { path: { id: img.id } } }));
                      await refresh();
                    } catch (err) {
                      notifications.show({ color: "red", message: (err as Error).message });
                    }
                  }}
                >
                  <IconTrash size={14} />
                </ActionIcon>
              )}
            </Box>
          ) : null,
        )}
      </SimpleGrid>
      {d.images.length === 0 && (
        <Text size="sm" c="dimmed">
          No images.
        </Text>
      )}
      {editor && (
        <Dropzone
          accept={[...IMAGE_MIME_TYPE, "image/tiff"]}
          onDrop={async (files) => {
            try {
              const r = await uploadImages(id, files);
              if (r.failed.length) notifications.show({ color: "yellow", message: `Not images: ${r.failed.join(", ")}` });
              await refresh();
            } catch (err) {
              notifications.show({ color: "red", message: (err as Error).message });
            }
          }}
          p="sm"
          data-testid="image-drop"
        >
          <Group gap="xs" justify="center" style={{ pointerEvents: "none" }}>
            <IconPhotoPlus size={18} />
            <Text size="sm" c="dimmed">
              Drop images here, or click to add
            </Text>
          </Group>
        </Dropzone>
      )}
    </Stack>
  );
}

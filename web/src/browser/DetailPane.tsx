// The selected model: a header (cover, title, status), one row of actions on its
// archive, and three tabs: its images, its details, and the files in the archive.
import {
  ActionIcon,
  Anchor,
  Badge,
  Box,
  Button,
  Center,
  CopyButton,
  Group,
  Image,
  Loader,
  Menu,
  SimpleGrid,
  Spoiler,
  Stack,
  Tabs,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { useEffect, useState } from "react";
import { Dropzone, IMAGE_MIME_TYPE } from "@mantine/dropzone";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconCheck,
  IconChevronDown,
  IconCopy,
  IconCube,
  IconDownload,
  IconEdit,
  IconExternalLink,
  IconFileZip,
  IconFolder,
  IconFolderOpen,
  IconPhotoPlus,
  IconTrash,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type Schemas, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import { inDesktopShell, meshpeekState, openFolder, openInMeshPeek, openInPacker, packerState } from "../desktop";
import { FileList } from "./ArchiveInfo";
import { detailFacts, type Fields, loadTab, middlePath, saveTab, type Tab, webLink } from "./detail";
import { ImageViewer, type ViewerImage } from "./ImageViewer";
import { baseName } from "../format";
import { ModelViewer } from "../viewer/ModelViewer";
import classes from "./DetailPane.module.css";

/** Upload images to a model (multipart; the server makes the thumbnails, at the view capture size for `view`). */
async function uploadImages(id: number, files: File[], view = false): Promise<Schemas["AddedImages"]> {
  const form = new FormData();
  for (const f of files) form.append("file", f, f.name);
  const resp = await fetch(`/api/models/${id}/images${view ? "?view=true" : ""}`, { method: "POST", body: form, credentials: "same-origin" });
  const body = await resp.json();
  if (!resp.ok) throw new ApiError(resp.status, body.error ?? resp.statusText);
  return body;
}

export function DetailPane({ id, onEdit }: { id: number | null; onEdit: (id: number) => void }) {
  const user = useUser();
  const editor = hasRole(user, "editor");
  // The image open in the full-size viewer (an index into the shown images).
  const [viewing, setViewing] = useState<number | null>(null);
  // The model viewer, open on a file (null: the archive's first).
  const [modelView, setModelView] = useState<{ file: string | null } | null>(null);
  const [tab, setTabState] = useState<Tab>(loadTab);
  const setTab = (t: Tab) => {
    setTabState(t);
    saveTab(t);
  };
  useEffect(() => {
    setViewing(null);
    setModelView(null);
  }, [id]);
  const meshpeek = useQuery({ queryKey: ["meshpeek"], queryFn: meshpeekState, enabled: inDesktopShell() });
  const packer = useQuery({ queryKey: ["packer"], queryFn: packerState, enabled: inDesktopShell() });
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
  const viewable: ViewerImage[] = d.images.flatMap((i) => (i.thumbnail ? [{ id: i.id, thumbnail: i.thumbnail }] : []));
  const f = d.fields as Fields;
  const path = (f.archive_path as string | null) ?? "";
  const title = (f.title as string) || baseName(path);
  const missing = f.is_missing === 1;
  const notify = (err: unknown) => notifications.show({ color: "red", message: String(err) });
  const tags = String(f.tags ?? "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const warnings = String(f.warning ?? "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  const link = webLink(f.url);
  const description = String(f.description ?? "").trim();
  const fileCount = typeof f.file_count === "number" ? f.file_count : 0;
  const cover = viewable[0];
  const local = d.client_path ?? null;
  const opens = [
    local && meshpeek.data?.program ? { label: "In MeshPeek", icon: IconExternalLink, run: () => openInMeshPeek(local) } : null,
    local && packer.data?.program ? { label: "In the archive program", icon: IconFileZip, run: () => openInPacker(local) } : null,
    local && inDesktopShell() ? { label: "Its folder", icon: IconFolderOpen, run: () => openFolder(local) } : null,
  ].filter((o) => o != null);
  const openImage = (imageId: number) => setViewing(viewable.findIndex((v) => v.id === imageId));
  return (
    <Stack gap="sm" p="sm" style={{ height: "100%", overflowY: "auto" }} data-testid="detail-pane">
      {tab !== "images" && cover && (
        <UnstyledButton onClick={() => openImage(cover.id)} className={classes.cover} aria-label="View the cover image" data-testid="detail-cover">
          <Image src={cover.thumbnail} radius="md" alt="" />
        </UnstyledButton>
      )}
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Title order={4}>{title}</Title>
        {editor && (
          <Button size="xs" variant="light" leftSection={<IconEdit size={14} />} onClick={() => onEdit(id)} style={{ flexShrink: 0 }}>
            Edit
          </Button>
        )}
      </Group>
      {missing && (
        <Badge color="red" variant="light" tt="none">
          Archive missing
        </Badge>
      )}
      <Group gap={6} data-testid="detail-actions">
        <Button size="xs" variant="light" leftSection={<IconCube size={14} />} disabled={missing} onClick={() => setModelView({ file: null })} data-testid="view-model">
          View model
        </Button>
        <Button component="a" href={`/api/models/${id}/archive`} download size="xs" variant="light" leftSection={<IconDownload size={14} />}>
          Download
        </Button>
        <Button size="xs" variant="light" leftSection={<IconFolder size={14} />} onClick={() => setTab("files")} data-testid="show-files">
          Files
        </Button>
        {opens.length > 0 && (
          <Menu position="bottom-start" shadow="md">
            <Menu.Target>
              <Button size="xs" variant="light" leftSection={<IconExternalLink size={14} />} rightSection={<IconChevronDown size={12} />}>
                Open
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {opens.map((o) => (
                <Menu.Item key={o.label} leftSection={<o.icon size={14} />} onClick={() => o.run().catch(notify)}>
                  {o.label}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
        )}
        <CopyButton value={local ?? path}>
          {({ copied, copy }) => (
            <Tooltip label={copied ? "Copied" : "Copy path"}>
              <ActionIcon size={30} variant="light" onClick={copy} aria-label="Copy path">
                {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              </ActionIcon>
            </Tooltip>
          )}
        </CopyButton>
      </Group>
      <Tabs value={tab} onChange={(t) => t && setTab(t as Tab)} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="images">Images{viewable.length ? ` ${viewable.length}` : ""}</Tabs.Tab>
          <Tabs.Tab value="details">Details</Tabs.Tab>
          <Tabs.Tab value="files" leftSection={warnings.length ? <IconAlertTriangle size={14} color="var(--mantine-color-orange-text)" /> : undefined}>
            Files{fileCount ? ` ${fileCount}` : ""}
          </Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="images" pt="sm">
          <Stack gap="sm">
            {viewable.length > 0 ? (
              <SimpleGrid cols={2} spacing="xs">
                {d.images.map((img) =>
                  img.thumbnail ? (
                    <Box key={img.id} className={classes.thumb}>
                      <UnstyledButton onClick={() => openImage(img.id)} display="block" style={{ cursor: "zoom-in" }} data-testid="detail-image">
                        <Image src={img.thumbnail} radius="md" alt="" loading="lazy" />
                      </UnstyledButton>
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
                              notify((err as Error).message);
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
            ) : (
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
                    notify((err as Error).message);
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
        </Tabs.Panel>
        <Tabs.Panel value="details" pt="sm">
          <Stack gap="sm" data-testid="detail-facts">
            <SimpleGrid cols={2} spacing="md" verticalSpacing={8}>
              {detailFacts(f).map(([label, value]) => (
                <Box key={label} miw={0}>
                  <Text size="xs" c="dimmed" tt="uppercase" fw={500} style={{ letterSpacing: "0.03em" }}>
                    {label}
                  </Text>
                  <Text size="sm" style={{ overflowWrap: "anywhere" }}>
                    {value}
                  </Text>
                </Box>
              ))}
            </SimpleGrid>
            {tags.length > 0 && (
              <Group gap={4}>
                {tags.map((t) => (
                  <Badge key={t} variant="light" size="sm" tt="none">
                    {t}
                  </Badge>
                ))}
              </Group>
            )}
            {description && (
              <Spoiler maxHeight={64} showLabel="More" hideLabel="Less">
                <Text size="sm" style={{ whiteSpace: "pre-line" }}>
                  {description}
                </Text>
              </Spoiler>
            )}
            {link && (
              <Anchor href={link} target="_blank" rel="noreferrer" size="sm" style={{ overflowWrap: "anywhere" }}>
                {link}
              </Anchor>
            )}
            <Group gap={6} wrap="nowrap" pt={6} style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
              <Tooltip label={path} multiline maw={360}>
                <Text size="xs" c="dimmed" style={{ flex: 1, minWidth: 0 }}>
                  {middlePath(path)}
                </Text>
              </Tooltip>
            </Group>
          </Stack>
        </Tabs.Panel>
        <Tabs.Panel value="files" pt="sm">
          <FileList id={id} warned={warnings.length > 0} onView={(file) => setModelView({ file })} />
        </Tabs.Panel>
      </Tabs>
      <ImageViewer images={viewable} index={viewing} onIndex={setViewing} onClose={() => setViewing(null)} />
      {modelView && (
        <ModelViewer
          id={id}
          title={title}
          file={modelView.file}
          clientPath={local}
          onAddImage={
            editor
              ? async (image) => {
                  const r = await uploadImages(id, [image], true);
                  if (r.failed.length) throw new Error("The server could not read the view as an image.");
                  await refresh();
                }
              : null
          }
          onClose={() => setModelView(null)}
        />
      )}
    </Stack>
  );
}

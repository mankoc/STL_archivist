// The detail pane's archive section: the stored warnings with the members that
// trigger them (Warning Details), and the files in the archive (Show Files, as a
// folder tree, those behind a warning highlighted), as the last crawl or rescan
// read the archive's catalog. The model files the server can show (`/viewable`, read
// from the archive now) open in the model viewer.
import { ActionIcon, Alert, Badge, Button, getTreeExpandedState, Group, Loader, Modal, Stack, Table, Text, Tooltip, Tree, useTree } from "@mantine/core";
import { IconAlertTriangle, IconChevronRight, IconCube, IconFile, IconFiles, IconFolder } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";
import { fileTree } from "./fileTree";
import { warningsByFile } from "./warnings";

export function ArchiveInfo({ id, warning, onView }: { id: number; warning: string | null; onView: (file: string) => void }) {
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
        {open === "files" && (
          <FileList
            id={id}
            warned={lines.length > 0}
            onView={(file) => {
              setOpen(null);
              onView(file);
            }}
          />
        )}
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

function FileList({ id, warned, onView }: { id: number; warned: boolean; onView: (file: string) => void }) {
  const q = useQuery({
    queryKey: ["model-files", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/archive-files", { params: { path: { id } } })),
  });
  // The same query as the model viewer's file list; none when the archive cannot be read.
  const models = useQuery({
    queryKey: ["model-viewable", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/viewable", { params: { path: { id } } })),
    retry: false,
  });
  const viewable = useMemo(() => new Set((models.data ?? []).map((f) => f.name)), [models.data]);
  // The same query as Warning details; it opens the archive, so only with warnings to explain.
  const details = useQuery({
    queryKey: ["model-warnings", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/warnings", { params: { path: { id } } })),
    enabled: warned,
    retry: false,
  });
  const warnings = useMemo(() => warningsByFile(details.data?.sections ?? []), [details.data]);
  if (q.isPending) return <Loader size="sm" />;
  if (q.isError) return <Alert color="red">{q.error.message}</Alert>;
  if (!q.data.length) {
    return (
      <Text size="sm" c="dimmed">
        No file list yet: Rescan reads it, or Settings → Maintenance → Read missing file lists for every archive without one.
      </Text>
    );
  }
  return <FileTreeView files={q.data} viewable={viewable} warnings={warnings} onView={onView} />;
}

/** The archive's folders and files, all folders open; model files have a view button, and files behind a warning stand out. */
function FileTreeView({ files, viewable, warnings, onView }: {
  files: { name: string; size?: number | null }[];
  /** The model files the viewer can show. */
  viewable: Set<string>;
  /** The warnings each file triggers. */
  warnings: Map<string, string[]>;
  onView: (file: string) => void;
}) {
  const tree = useMemo(() => fileTree(files), [files]);
  const controller = useTree({ initialExpandedState: getTreeExpandedState(tree.data, "*") });
  return (
    <Stack gap={4} data-testid="file-list">
      <Group gap="xs" justify="space-between">
        <Text size="xs" c="dimmed">
          {files.length} file{files.length === 1 ? "" : "s"}
          {warnings.size > 0 && `, ${warnings.size} with a warning`}
        </Text>
        <Group gap={4}>
          <Button size="compact-xs" variant="subtle" onClick={() => controller.expandAllNodes()}>Expand all</Button>
          <Button size="compact-xs" variant="subtle" onClick={() => controller.collapseAllNodes()}>Collapse all</Button>
        </Group>
      </Group>
      <Tree
        data={tree.data}
        tree={controller}
        levelOffset={18}
        renderNode={({ node, expanded, hasChildren, elementProps }) => {
          const info = tree.info.get(node.value)!;
          const reasons = info.kind === "file" ? warnings.get(info.name!) : undefined;
          // A folder stands out too, so a closed one does not hide its files.
          const warned = reasons != null || (info.files ?? []).some((f) => warnings.has(f));
          const color = warned ? "orange" : undefined;
          const name = <Text size="xs" c={color} fw={reasons ? 600 : undefined} style={{ flex: 1, minWidth: 0, wordBreak: "break-all" }}>{node.label}</Text>;
          return (
            <Group gap={6} wrap="nowrap" {...elementProps} style={{ ...elementProps.style, minHeight: 24 }}>
              {hasChildren ? (
                <IconChevronRight size={14} style={{ flexShrink: 0, transform: expanded ? "rotate(90deg)" : undefined }} />
              ) : (
                <span style={{ width: 14, flexShrink: 0 }} />
              )}
              {info.kind === "folder" ? (
                <IconFolder size={14} color={warned ? "var(--mantine-color-orange-text)" : undefined} style={{ flexShrink: 0 }} />
              ) : reasons ? (
                <IconAlertTriangle size={14} color="var(--mantine-color-orange-text)" style={{ flexShrink: 0 }} />
              ) : (
                <IconFile size={14} style={{ flexShrink: 0 }} />
              )}
              {reasons ? <Tooltip label={reasons.join(", ")}>{name}</Tooltip> : name}
              {info.kind === "folder" ? (
                <Text size="xs" c="dimmed" w={170} ta="right">{info.files!.length} file{info.files!.length === 1 ? "" : "s"} · {formatSize(info.size)}</Text>
              ) : (
                <Text size="xs" w={80} ta="right">{formatSize(info.size)}</Text>
              )}
              <span style={{ width: 28, flexShrink: 0 }}>
                {info.kind === "file" && viewable.has(info.name!) && (
                  <Tooltip label="View">
                    <ActionIcon
                      size="sm"
                      variant="subtle"
                      aria-label={`View ${info.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onView(info.name!);
                      }}
                    >
                      <IconCube size={14} />
                    </ActionIcon>
                  </Tooltip>
                )}
              </span>
            </Group>
          );
        }}
      />
    </Stack>
  );
}

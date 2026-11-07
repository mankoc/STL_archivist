// The archive's files, as the last crawl or rescan read its catalog: the model's
// warnings and why each is raised, then a folder tree with the files behind them and the
// folders holding them highlighted. The
// model files the server can show (`/viewable`, read from the archive now) open in the
// model viewer. The detail pane's Files tab.
import { ActionIcon, Alert, Button, getTreeExpandedState, Group, Loader, Stack, Text, Tooltip, Tree, useTree } from "@mantine/core";
import { IconAlertTriangle, IconChevronRight, IconCube, IconFile, IconFolder } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";
import { fileTree } from "./fileTree";
import { warningsByFile } from "./warnings";

export function FileList({ id, warned, onView }: { id: number; warned: boolean; onView: (file: string) => void }) {
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
  return (
    <Stack gap="sm">
      {details.data && details.data.sections.length > 0 && (
        <Stack gap={4} data-testid="warning-sections">
          {details.data.sections.map((s) => (
            <Group key={s.warning} gap={6} wrap="nowrap" align="flex-start">
              <IconAlertTriangle size={14} color="var(--mantine-color-orange-text)" style={{ flexShrink: 0, marginTop: 2 }} />
              <Text size="xs">
                <Text span size="xs" fw={600} c="orange">
                  {s.warning}
                </Text>
                <Text span size="xs" c="dimmed">
                  {" — "}
                  {whyText(s.members.length)}
                </Text>
              </Text>
            </Group>
          ))}
          {details.data.error && (
            <Text size="xs" c="dimmed">
              {details.data.error}
            </Text>
          )}
        </Stack>
      )}
      <FileTreeView files={q.data} viewable={viewable} warnings={warnings} onView={onView} />
    </Stack>
  );
}

/** Why a warning is raised, from the number of files that trigger it. */
export function whyText(files: number): string {
  if (files === 0) return "raised by something the archive lacks, not by a file";
  return `${files} file${files === 1 ? "" : "s"}, marked below`;
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
        levelOffset={12}
        renderNode={({ node, expanded, hasChildren, elementProps }) => {
          const info = tree.info.get(node.value)!;
          const reasons = info.kind === "file" ? warnings.get(info.name!) : undefined;
          // A folder stands out too, so a closed one does not hide its files.
          const warned = reasons != null || (info.files ?? []).some((f) => warnings.has(f));
          const color = warned ? "orange" : undefined;
          const name = <Text size="xs" c={color} fw={reasons ? 600 : undefined} style={{ flex: 1, minWidth: 60, overflowWrap: "anywhere" }}>{node.label}</Text>;
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
                <Text size="xs" c="dimmed" ta="right" style={{ flexShrink: 0, whiteSpace: "nowrap" }}>{info.files!.length} · {formatSize(info.size)}</Text>
              ) : (
                <Text size="xs" ta="right" style={{ flexShrink: 0, whiteSpace: "nowrap" }}>{formatSize(info.size)}</Text>
              )}
              <span style={{ width: 24, flexShrink: 0 }}>
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

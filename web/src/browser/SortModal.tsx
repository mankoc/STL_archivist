// Sort files: move the selected archives into a folder tree built from a path
// pattern, after a preview (the desktop's Sort / Organise Files dialog).
import { Alert, Badge, Button, Group, Modal, Stack, Table, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { FolderPicker } from "../jobs/FolderPicker";
import { PatternTools } from "./PatternTools";
import classes from "./SortModal.module.css";

const PATTERN_KEY = "stl.sort.pattern";
/** The size the dialog was resized to, remembered in this browser. */
const SIZE_KEY = "stl.sort.size";
const STATUS_COLOR: Record<string, string> = { OK: "green", COLLISION: "yellow", MISS: "red", SKIP: "gray", ERROR: "red", FILE: "blue" };

function stored(): string {
  try {
    return localStorage.getItem(PATTERN_KEY) ?? "{collection}/{creator}/{title}";
  } catch {
    return "{collection}/{creator}/{title}";
  }
}

function storedSize(): { width?: string; height?: string } {
  try {
    const v = JSON.parse(localStorage.getItem(SIZE_KEY) ?? "{}");
    return typeof v?.width === "string" && typeof v?.height === "string" ? { width: v.width, height: v.height } : {};
  } catch {
    return {};
  }
}

/** Remembers the dialog's size once its corner has been dragged (or a size restored). */
function rememberSize(stack: HTMLDivElement | null) {
  const content = stack?.closest<HTMLElement>(".mantine-Modal-content");
  if (!content || typeof ResizeObserver === "undefined") return;
  const observer = new ResizeObserver(() => {
    const { width, height } = content.style;
    if (!width) return;
    try {
      localStorage.setItem(SIZE_KEY, JSON.stringify({ width, height }));
    } catch {
      /* per-viewer convenience only */
    }
  });
  observer.observe(content);
  return () => observer.disconnect();
}

export function SortModal({ ids, opened, onClose }: { ids: number[]; opened: boolean; onClose: () => void }) {
  const [target, setTarget] = useState("");
  const [pattern, setPattern] = useState(stored);
  const [picker, setPicker] = useState(false);
  const [rows, setRows] = useState<Schemas["SortRow"][] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const moving = (rows ?? []).filter((r) => r.status === "OK" || r.status === "COLLISION" || r.status === "FILE").length;
  const changePattern = (p: string) => {
    setPattern(p);
    setRows(null);
  };

  const preview = async () => {
    setError(null);
    try {
      setRows(unwrap(await api.POST("/api/sort/preview", { body: { ids, target_dir: target, pattern } })));
      try {
        localStorage.setItem(PATTERN_KEY, pattern);
      } catch {
        /* per-viewer convenience only */
      }
    } catch (err) {
      setRows(null);
      setError((err as Error).message);
    }
  };
  const move = async () => {
    try {
      unwrap(await api.POST("/api/jobs", { body: { kind: "sort_files", ids, target_dir: target, pattern } }));
      notifications.show({ message: "Moving the archives: follow it on the Jobs page" });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={`Sort ${ids.length} archive(s)`}
      size="xl"
      classNames={{ content: classes.content, body: classes.body }}
      styles={{ content: opened ? storedSize() : undefined }}
    >
      <Stack ref={rememberSize} className={classes.stack}>
        <Group align="flex-end">
          <TextInput label="Target folder" value={target} onChange={(e) => { setTarget(e.currentTarget.value); setRows(null); }} style={{ flex: 1 }} />
          <Button variant="default" onClick={() => setPicker(true)}>Browse…</Button>
        </Group>
        <Group align="flex-end" gap="xs" wrap="nowrap">
          <TextInput
            label="Path pattern"
            description="Folders, then the file name, for example {creator}/{type}/{date} - {title}"
            value={pattern}
            onChange={(e) => changePattern(e.currentTarget.value)}
            style={{ flex: 1 }}
            data-testid="sort-pattern"
          />
          <PatternTools pattern={pattern} onChange={changePattern} mode="sort" />
        </Group>
        {error && <Alert color="red">{error}</Alert>}
        <Group>
          <Button variant="light" onClick={preview} disabled={!target || !pattern} data-testid="sort-preview">Preview</Button>
          <Button onClick={move} disabled={!moving} data-testid="sort-move">Move {moving || ""} file(s)</Button>
        </Group>
        {rows && (
          <div className={classes.rows}>
            <Table fz="xs" striped data-testid="sort-rows">
              <Table.Tbody>
                {rows.map((r) => (
                  <Table.Tr key={`${r.id} ${r.from}`}>
                    <Table.Td w={100}><Badge size="xs" color={STATUS_COLOR[r.status] ?? "gray"}>{r.status}</Badge></Table.Td>
                    <Table.Td style={{ wordBreak: "break-all" }}>{r.from}</Table.Td>
                    <Table.Td style={{ wordBreak: "break-all" }}>{r.to}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>
        )}
        <Text size="xs" c="dimmed">
          When every archive of a folder goes to one folder, the folder’s other files (pictures, links, info.json) go with
          them (FILE). Every file is moved, or none: if one move fails the others go back. Needs “file sorting” in the library
          settings.
        </Text>
      </Stack>
      <FolderPicker canCreate opened={picker} onClose={() => setPicker(false)} onPick={(p) => { setTarget(p); setPicker(false); setRows(null); }} />
    </Modal>
  );
}

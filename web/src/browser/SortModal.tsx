// Sort files: move the selected archives into a folder tree built from a path
// pattern, after a preview (the desktop's Sort / Organise Files dialog).
import { Alert, Badge, Button, Group, Modal, ScrollArea, Stack, Table, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { FolderPicker } from "../jobs/FolderPicker";

const PATTERN_KEY = "stl.sort.pattern";
const STATUS_COLOR: Record<string, string> = { OK: "green", COLLISION: "yellow", MISS: "red", SKIP: "gray", ERROR: "red" };

function stored(): string {
  try {
    return localStorage.getItem(PATTERN_KEY) ?? "{collection}/{creator}/{title}";
  } catch {
    return "{collection}/{creator}/{title}";
  }
}

export function SortModal({ ids, opened, onClose }: { ids: number[]; opened: boolean; onClose: () => void }) {
  const [target, setTarget] = useState("");
  const [pattern, setPattern] = useState(stored);
  const [picker, setPicker] = useState(false);
  const [rows, setRows] = useState<Schemas["SortRow"][] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const moving = (rows ?? []).filter((r) => r.status === "OK" || r.status === "COLLISION").length;

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
    <Modal opened={opened} onClose={onClose} title={`Sort ${ids.length} archive(s)`} size="xl">
      <Stack>
        <Group align="flex-end">
          <TextInput label="Target folder" value={target} onChange={(e) => { setTarget(e.currentTarget.value); setRows(null); }} style={{ flex: 1 }} />
          <Button variant="default" onClick={() => setPicker(true)}>Browse…</Button>
        </Group>
        <TextInput
          label="Path pattern"
          description="Folders, then the file name: {collection}, {subcollection}, {sculptor}, {creator}, {title}, {date}, {release}, {source}, {sequence}; {title:20} cuts to 20 characters."
          value={pattern}
          onChange={(e) => { setPattern(e.currentTarget.value); setRows(null); }}
        />
        {error && <Alert color="red">{error}</Alert>}
        <Group>
          <Button variant="light" onClick={preview} disabled={!target || !pattern} data-testid="sort-preview">Preview</Button>
          <Button onClick={move} disabled={!moving} data-testid="sort-move">Move {moving || ""} file(s)</Button>
        </Group>
        {rows && (
          <ScrollArea.Autosize mah="45vh">
            <Table fz="xs" striped data-testid="sort-rows">
              <Table.Tbody>
                {rows.map((r) => (
                  <Table.Tr key={r.id}>
                    <Table.Td w={100}><Badge size="xs" color={STATUS_COLOR[r.status] ?? "gray"}>{r.status}</Badge></Table.Td>
                    <Table.Td style={{ wordBreak: "break-all" }}>{r.from}</Table.Td>
                    <Table.Td style={{ wordBreak: "break-all" }}>{r.to}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea.Autosize>
        )}
        <Text size="xs" c="dimmed">
          Every file is moved, or none: if one move fails the others go back. Needs “file sorting” in the library settings.
        </Text>
      </Stack>
      <FolderPicker opened={picker} onClose={() => setPicker(false)} onPick={(p) => { setTarget(p); setPicker(false); setRows(null); }} />
    </Modal>
  );
}

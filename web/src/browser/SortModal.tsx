// Sort files: move the selected archives into a folder tree built from a path
// pattern, after a preview (the desktop's Sort / Organise Files dialog).
import { ActionIcon, Alert, Badge, Button, Code, Group, Menu, Modal, Popover, ScrollArea, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconBookmark, IconDeviceFloppy, IconHelp, IconX } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { FolderPicker } from "../jobs/FolderPicker";
import { SAVED_PATTERNS, savedPatterns, TOKENS, withoutPattern, withPattern } from "./patterns";

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
  const queryClient = useQueryClient();
  const prefs = useQuery({ queryKey: ["me-settings"], queryFn: async () => unwrap(await api.GET("/api/me/settings")) as Record<string, unknown> });
  const saved = savedPatterns(prefs.data?.[SAVED_PATTERNS]);
  const changePattern = (p: string) => {
    setPattern(p);
    setRows(null);
  };
  const storeSaved = async (list: string[]) => {
    try {
      unwrap(await api.PUT("/api/me/settings/{key}", { params: { path: { key: SAVED_PATTERNS } }, body: list as never }));
      await queryClient.invalidateQueries({ queryKey: ["me-settings"] });
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
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
    <Modal opened={opened} onClose={onClose} title={`Sort ${ids.length} archive(s)`} size="xl">
      <Stack>
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
          <Popover position="bottom-end" shadow="md" withArrow>
            <Popover.Target>
              <Tooltip label="Tokens">
                <ActionIcon variant="default" size="lg" aria-label="Pattern tokens" data-testid="pattern-help">
                  <IconHelp size={18} />
                </ActionIcon>
              </Tooltip>
            </Popover.Target>
            <Popover.Dropdown maw={420}>
              <Text size="xs" mb={6}>
                Each <Code>/</Code> starts a folder; the last part is the file name (the extension is kept). Text around tokens
                stays as typed. Click a token to add it.
              </Text>
              <Table fz="xs" withRowBorders={false} verticalSpacing={2}>
                <Table.Tbody>
                  {TOKENS.map((t) => (
                    <Table.Tr key={t.token}>
                      <Table.Td>
                        {[t.token, ...t.aliases].map((name) => (
                          <Code key={name} mr={4} style={{ cursor: "pointer" }} onClick={() => changePattern(`${pattern}{${name}}`)}>
                            {`{${name}}`}
                          </Code>
                        ))}
                      </Table.Td>
                      <Table.Td>{t.field}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              <Text size="xs" c="dimmed" mt={6}>
                <Code>{"{title:20}"}</Code> keeps the first 20 characters, so <Code>{"{creator:1}/{creator}/{title}"}</Code> puts Sanix
                under <Code>S/Sanix/</Code>. Case does not matter. A missing value becomes <Code>_unknown</Code>; characters not
                allowed in file names become <Code>_</Code>.
              </Text>
            </Popover.Dropdown>
          </Popover>
          <Menu position="bottom-end" shadow="md">
            <Menu.Target>
              <Button variant="default" leftSection={<IconBookmark size={16} />} data-testid="saved-patterns">Saved</Button>
            </Menu.Target>
            <Menu.Dropdown maw={480}>
              <Menu.Item leftSection={<IconDeviceFloppy size={14} />} disabled={!pattern.trim() || saved.includes(pattern.trim())}
                onClick={() => void storeSaved(withPattern(saved, pattern))} data-testid="save-pattern">
                Save this pattern
              </Menu.Item>
              {saved.length > 0 && <Menu.Divider />}
              {saved.length === 0 && <Menu.Label>No saved patterns yet</Menu.Label>}
              {saved.map((p) => (
                <Menu.Item key={p} onClick={() => changePattern(p)}
                  rightSection={
                    <ActionIcon size="xs" variant="subtle" color="gray" aria-label={`Forget ${p}`}
                      onClick={(e) => { e.stopPropagation(); void storeSaved(withoutPattern(saved, p)); }}>
                      <IconX size={12} />
                    </ActionIcon>
                  }>
                  <Text size="xs" ff="monospace">{p}</Text>
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
        </Group>
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
      <FolderPicker canCreate opened={picker} onClose={() => setPicker(false)} onPick={(p) => { setTarget(p); setPicker(false); setRows(null); }} />
    </Modal>
  );
}

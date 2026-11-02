// The mass editor (the desktop's Mass Edit dialog) for the selected models.
import {
  Autocomplete, Button, Checkbox, Divider, Drawer, Group, Portal, ScrollArea, Select, SimpleGrid, Stack, Switch, Table, Text, TextInput,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { type PointerEvent as ReactPointerEvent, useEffect, useState } from "react";

import { api, unwrap } from "../api/client";
import { CLEAR_PRESUPPORTS, emptyForm, FIND_FIELDS, MANUAL, type MassForm, toRequest } from "./massEdit";
import { clampWidth, loadWidth, MASS_EDIT, saveWidth } from "./paneWidth";
import { PatternTools } from "./PatternTools";
import classes from "./MassEditDrawer.module.css";

const LIMIT = 50;
/** Paths parsed as the pattern is typed; *Show all parsed values* parses every selected one. */
const PREVIEW_ROWS = 5;

export function MassEditDrawer({ ids, opened, onClose }: { ids: number[]; opened: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<MassForm>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [all, setAll] = useState(false);
  // The drawer's width, dragged by its left edge.
  const [width, setWidth] = useState(() => loadWidth(MASS_EDIT.key, MASS_EDIT.fallback, MASS_EDIT.min));
  const [dragging, setDragging] = useState(false);
  const startDrag = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    setDragging(true);
    let w = width;
    const move = (ev: PointerEvent) => {
      w = clampWidth(window.innerWidth - ev.clientX, window.innerWidth, MASS_EDIT.maxShare, MASS_EDIT.min);
      setWidth(w);
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      setDragging(false);
      saveWidth(w, MASS_EDIT.key);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
  };
  const resetWidth = () => {
    setWidth(MASS_EDIT.fallback);
    saveWidth(MASS_EDIT.fallback, MASS_EDIT.key);
  };
  useEffect(() => {
    if (opened) {
      setForm(emptyForm());
      setAll(false);
    }
  }, [opened]);
  const [pattern] = useDebouncedValue(form.pattern.trim(), 300);
  const parsedIds = all ? ids : ids.slice(0, PREVIEW_ROWS);
  const parsed = useQuery({
    queryKey: ["parse-paths", parsedIds, pattern],
    queryFn: async () => unwrap(await api.POST("/api/models/parse-paths", { body: { ids: parsedIds, pattern } })),
    enabled: opened && pattern !== "",
    placeholderData: keepPreviousData,
  });
  const preview = pattern && parsed.data ? parsed.data.map((r) => ({ archive_path: r.archive_path, fields: r.fields as [string, string][] })) : null;
  const choices = useQuery({
    queryKey: ["lookups", "choices"],
    queryFn: async () => unwrap(await api.GET("/api/editor/choices")) as Record<string, string[]>,
    enabled: opened,
  });
  // Subcollection names repeat across collections, and an Autocomplete throws on duplicate options.
  const list = (k: string) => [...new Set(choices.data?.[k] ?? [])];
  const set = <K extends keyof MassForm>(k: K, v: MassForm[K]) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      const r = unwrap(await api.POST("/api/models/mass-edit", { body: toRequest(form, ids) }));
      notifications.show({
        message: `Updated ${r.models} model(s)${form.pattern.trim() ? `; the pattern matched ${r.pattern_matches}` : ""}.`,
      });
      for (const key of ["rows", "tree", "model", "editor", "lookups"]) await queryClient.invalidateQueries({ queryKey: [key] });
      onClose();
    } catch (err) {
      notifications.show({ color: "red", title: "Not saved", message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  };
  const columns = preview ? [...new Set(preview.flatMap((p) => p.fields.map(([k]) => k)))] : [];
  const matched = preview?.filter((p) => p.fields.length).length ?? 0;

  return (
    <Drawer opened={opened} onClose={onClose} position="right" size={Math.min(width, window.innerWidth)} title={<Text fw={600}>Mass edit {ids.length} model(s)</Text>}>
      {/* In a portal: the drawer's slide-in transform would move a fixed element inside it. */}
      <Portal>
        <div
          className={classes.splitter}
          style={{ right: Math.min(width, window.innerWidth) - 4 }}
          data-dragging={dragging || undefined}
          onPointerDown={startDrag}
          onDoubleClick={resetWidth}
          title="Drag to resize; double-click to reset"
          data-testid="mass-edit-splitter"
        />
      </Portal>
      <Stack gap="sm" data-testid="mass-editor">
        <Group align="flex-end" gap="xs" wrap="nowrap">
          <TextInput
            label="Path pattern"
            description="Values parsed from each archive path, for example {sculptor}/{release} - {title}"
            value={form.pattern}
            onChange={(e) => set("pattern", e.currentTarget.value)}
            flex={1}
            data-testid="mass-pattern"
          />
          <PatternTools pattern={form.pattern} onChange={(p) => set("pattern", p)} mode="parse" />
        </Group>
        {parsed.isError && pattern && (
          <Text size="xs" c="red">
            {parsed.error.message}
          </Text>
        )}
        {preview && (
          <>
            <Group justify="space-between">
              <Text size="xs" c="dimmed">
                {all || ids.length <= PREVIEW_ROWS
                  ? `${matched} of ${preview.length} path(s) match.`
                  : `The first ${preview.length} of ${ids.length} paths: ${matched} match.`}
              </Text>
              {!all && ids.length > PREVIEW_ROWS && (
                <Button size="compact-xs" variant="default" onClick={() => setAll(true)} data-testid="mass-parse-all">
                  Show all parsed values
                </Button>
              )}
            </Group>
            <ScrollArea.Autosize mah={220}>
              <Table fz="xs" withTableBorder>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Archive</Table.Th>
                    {columns.map((c) => (
                      <Table.Th key={c}>{c}</Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {preview.map((p) => (
                    <Table.Tr key={p.archive_path} c={p.fields.length ? undefined : "dimmed"}>
                      <Table.Td style={{ wordBreak: "break-all" }}>{p.archive_path}</Table.Td>
                      {columns.map((c) => (
                        <Table.Td key={c}>{p.fields.find(([k]) => k === c)?.[1] ?? ""}</Table.Td>
                      ))}
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea.Autosize>
          </>
        )}
        <Switch label="Parsed and typed values replace existing ones (otherwise they fill empty fields)" checked={form.overwrite} onChange={(e) => set("overwrite", e.currentTarget.checked)} />
        <Divider label="Fields" labelPosition="left" />
        <Text size="xs" c="dimmed">
          Empty fields are left alone. Values may use {"{tokens}"} from the pattern and {"{filename}"}, {"{folder}"}.
        </Text>
        <SimpleGrid cols={2} spacing="xs">
          {MANUAL.map(([k, label]) => (
            <Group key={k} align="flex-end" wrap="nowrap" gap="xs">
              <TextInput
                label={label}
                value={form.manual[k]}
                disabled={form.clear[k]}
                onChange={(e) => setForm((f) => ({ ...f, manual: { ...f.manual, [k]: e.currentTarget.value } }))}
                flex={1}
                data-testid={`mass-${k}`}
              />
              <Checkbox label="Clear" mb={8} checked={form.clear[k]} onChange={(e) => setForm((f) => ({ ...f, clear: { ...f.clear, [k]: e.currentTarget.checked } }))} />
            </Group>
          ))}
          <Select
            label="Presupports"
            value={form.presupports}
            onChange={(v) => set("presupports", v ?? "")}
            data={[
              { value: "", label: "— no change —" },
              { value: CLEAR_PRESUPPORTS, label: "(clear)" },
              ...["No", "Presupported", "Lychee", "Chitubox"].map((v) => ({ value: v, label: v })),
            ]}
            allowDeselect={false}
          />
          <Autocomplete label="Sculptor" value={form.sculptor} onChange={(v) => set("sculptor", v)} data={list("sculptors")} limit={LIMIT} />
          <Autocomplete label="Release" value={form.release} onChange={(v) => set("release", v)} data={list("releases")} limit={LIMIT} />
          <Autocomplete label="Collection" value={form.collection} onChange={(v) => set("collection", v)} data={list("collections")} limit={LIMIT} />
          <Autocomplete label="Subcollection" value={form.subcollection} onChange={(v) => set("subcollection", v)} data={list("subcollections")} limit={LIMIT} />
          <Autocomplete label="Category" value={form.category} onChange={(v) => set("category", v)} data={list("categories")} limit={LIMIT} />
          <TextInput label="Scale" description="Comma-separated, added" value={form.scale} onChange={(e) => set("scale", e.currentTarget.value)} />
          <TextInput label="Tags" description="Comma-separated, added" value={form.tags} onChange={(e) => set("tags", e.currentTarget.value)} data-testid="mass-tags" />
        </SimpleGrid>
        <Divider label="Find & replace" labelPosition="left" />
        <SimpleGrid cols={2} spacing="xs">
          <TextInput label="Find" value={form.find} onChange={(e) => set("find", e.currentTarget.value)} />
          <TextInput label="Replace with" value={form.replace} onChange={(e) => set("replace", e.currentTarget.value)} />
        </SimpleGrid>
        <Group gap="md">
          {FIND_FIELDS.map((f) => (
            <Checkbox
              key={f}
              label={f}
              checked={form.findFields.includes(f)}
              onChange={(e) => set("findFields", e.currentTarget.checked ? [...form.findFields, f] : form.findFields.filter((x) => x !== f))}
            />
          ))}
          <Checkbox label="Case-sensitive" checked={form.findCase} onChange={(e) => set("findCase", e.currentTarget.checked)} />
          <Checkbox label="Regular expression" checked={form.findRegex} onChange={(e) => set("findRegex", e.currentTarget.checked)} />
        </Group>
        <Switch label="Trim leading and trailing whitespace" checked={form.trim} onChange={(e) => set("trim", e.currentTarget.checked)} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={saving} data-testid="mass-save">
            Apply to {ids.length} model(s)
          </Button>
        </Group>
      </Stack>
    </Drawer>
  );
}

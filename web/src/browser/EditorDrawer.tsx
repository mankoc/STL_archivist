// The model editor (the desktop's Edit Model dialog) in a drawer: every field,
// with completion lists from the library. Empty fields clear the value.
import { Autocomplete, Button, Drawer, Group, Loader, NumberInput, SimpleGrid, Stack, TagsInput, Text, Textarea, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";

type Form = Required<Schemas["EditorSave"]>;

const EMPTY_FORM: Form = {
  title: "", creator: "", source: "", date: "", presupports: "", url: "", description: "", sequence: 0,
  release: "", sculptor: "", collection: "", subcollection: "", category: "", scales: [], tags: [],
};

const LIMIT = 50;

export function EditorDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const model = useQuery({
    queryKey: ["editor", id],
    queryFn: async () => unwrap(await api.GET("/api/models/{id}/editor", { params: { path: { id: id! } } })),
    enabled: id != null,
  });
  const choices = useQuery({
    queryKey: ["lookups", "choices"],
    queryFn: async () => unwrap(await api.GET("/api/editor/choices")) as Record<string, string[]>,
    enabled: id != null,
  });
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const m = model.data;
    if (!m) return;
    setForm({
      title: m.title ?? "", creator: m.creator ?? "", source: m.source ?? "", date: m.date,
      presupports: m.presupports ?? "", url: m.url ?? "", description: m.description ?? "",
      sequence: m.sequence ?? 0, release: m.release ?? "", sculptor: m.sculptor ?? "",
      collection: m.collection ?? "", subcollection: m.subcollection ?? "", category: m.category ?? "",
      scales: m.scales, tags: m.tags,
    });
  }, [model.data]);
  const subs = useQuery({
    queryKey: ["lookups", "subcollections", form.collection],
    queryFn: async () =>
      unwrap(await api.GET("/api/editor/subcollections", { params: { query: { collection: form.collection } } })),
    enabled: id != null && form.collection !== "",
  });
  const set = <K extends keyof Form>(k: K) => (v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));
  const list = (k: string) => choices.data?.[k] ?? [];

  const save = async () => {
    setSaving(true);
    try {
      unwrap(await api.PUT("/api/models/{id}/editor", { params: { path: { id: id! } }, body: form }));
      notifications.show({ message: "Saved." });
      for (const key of ["rows", "tree", "model", "editor", "lookups"]) {
        await queryClient.invalidateQueries({ queryKey: [key] });
      }
      onClose();
    } catch (err) {
      notifications.show({ color: "red", title: "Could not save", message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer opened={id != null} onClose={onClose} position="right" size="lg" title={<Text fw={600}>Edit model</Text>}>
      {model.isPending || !model.data ? (
        <Loader size="sm" />
      ) : (
        <Stack gap="sm" data-testid="editor">
          <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
            {model.data.archive_path}
          </Text>
          <Autocomplete label="Title" value={form.title} onChange={set("title")} data={list("titles")} limit={LIMIT} />
          <SimpleGrid cols={2}>
            <Autocomplete label="Creator" value={form.creator} onChange={set("creator")} data={list("creators")} limit={LIMIT} />
            <Autocomplete label="Type" value={form.source} onChange={set("source")} data={list("sources")} limit={LIMIT} />
            <Autocomplete label="Date" description="YYYY-MM or YYYY" value={form.date} onChange={set("date")} data={list("dates")} limit={LIMIT} />
            <NumberInput label="Sequence" description="0 clears" min={0} value={form.sequence} onChange={(v) => set("sequence")(Number(v) || 0)} />
            <Autocomplete label="Sculptor" value={form.sculptor} onChange={set("sculptor")} data={list("sculptors")} limit={LIMIT} />
            <Autocomplete label="Release" value={form.release} onChange={set("release")} data={list("releases")} limit={LIMIT} />
            <Autocomplete label="Collection" value={form.collection} onChange={set("collection")} data={list("collections")} limit={LIMIT} />
            <Autocomplete
              label="Subcollection"
              value={form.subcollection}
              onChange={set("subcollection")}
              data={subs.data ?? []}
              disabled={form.collection === ""}
              limit={LIMIT}
            />
            <Autocomplete label="Category" value={form.category} onChange={set("category")} data={list("categories")} limit={LIMIT} />
            <Autocomplete
              label="Presupports"
              value={form.presupports}
              onChange={set("presupports")}
              data={["No", "Presupported", "Lychee", "Chitubox"]}
            />
          </SimpleGrid>
          <TagsInput label="Scales" value={form.scales} onChange={set("scales")} data={list("scales")} limit={LIMIT} clearable />
          <TagsInput label="Tags" value={form.tags} onChange={set("tags")} data={list("tags")} limit={LIMIT} clearable />
          <TextInput label="URL" value={form.url} onChange={(e) => set("url")(e.currentTarget.value)} />
          <Textarea label="Description" autosize minRows={2} maxRows={8} value={form.description} onChange={(e) => set("description")(e.currentTarget.value)} />
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={save} loading={saving}>
              Save
            </Button>
          </Group>
        </Stack>
      )}
    </Drawer>
  );
}

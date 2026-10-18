// The lookup editors (Tag, Category, Collection, Subcollection, Scale and
// Author editors in the desktop app). Reading needs a session, changes the
// editor role.
import {
  ActionIcon, Autocomplete, Button, Card, Checkbox, Group, Modal, Radio, ScrollArea, Select, Stack, Table, Tabs, Text, TextInput, Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconId, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";

type Kind = "tag" | "category" | "collection" | "subcollection" | "scale";
type Row = Schemas["LookupRow"];

const KINDS: { kind: Kind; label: string }[] = [
  { kind: "tag", label: "Tags" },
  { kind: "category", label: "Categories" },
  { kind: "collection", label: "Collections" },
  { kind: "subcollection", label: "Subcollections" },
  { kind: "scale", label: "Scales" },
];

function fail(err: unknown) {
  notifications.show({ color: "red", message: (err as Error).message });
}

export function LookupsPage() {
  return (
    <Tabs defaultValue="tag" p="md" keepMounted={false}>
      <Tabs.List mb="md">
        {KINDS.map((k) => (
          <Tabs.Tab key={k.kind} value={k.kind}>
            {k.label}
          </Tabs.Tab>
        ))}
        <Tabs.Tab value="authors">Creators</Tabs.Tab>
      </Tabs.List>
      {KINDS.map((k) => (
        <Tabs.Panel key={k.kind} value={k.kind}>
          <LookupEditor kind={k.kind} />
        </Tabs.Panel>
      ))}
      <Tabs.Panel value="authors">
        <Authors />
      </Tabs.Panel>
    </Tabs>
  );
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return async () => {
    for (const key of ["lookups", "tree", "rows", "model"]) await queryClient.invalidateQueries({ queryKey: [key] });
  };
}

function LookupEditor({ kind }: { kind: Kind }) {
  const editor = hasRole(useUser(), "editor");
  const refresh = useInvalidate();
  const rows = useQuery({
    queryKey: ["lookups", kind],
    queryFn: async () => unwrap(await api.GET("/api/lookups/{kind}", { params: { path: { kind } } })),
  });
  const collections = useQuery({
    queryKey: ["lookups", "collection"],
    queryFn: async () => unwrap(await api.GET("/api/lookups/{kind}", { params: { path: { kind: "collection" } } })),
    enabled: kind === "subcollection",
  });
  const [search, setSearch] = useState("");
  const [checked, setChecked] = useState<number[]>([]);
  const [newName, setNewName] = useState("");
  const [newParent, setNewParent] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<Row | null>(null);
  const [renameTo, setRenameTo] = useState("");
  const [deleting, setDeleting] = useState<{ row: Row; info: Schemas["DeleteInfo"] } | null>(null);
  const [merging, setMerging] = useState(false);
  const [keep, setKeep] = useState<string>("");
  const [tagTarget, setTagTarget] = useState("");
  const unused = useQuery({
    queryKey: ["lookups", "unused-tags"],
    queryFn: async () => unwrap(await api.GET("/api/tags/unused")).count,
    enabled: kind === "tag",
  });

  const list = (rows.data ?? []).filter((r) => r.name.toLowerCase().includes(search.toLowerCase()));
  const byId = new Map((rows.data ?? []).map((r) => [r.id, r]));
  const label = (r: Row) => (r.collection ? `${r.name} (${r.collection})` : r.name);
  const toggle = (id: number) => setChecked((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
  const act = async (f: () => Promise<unknown>) => {
    try {
      await f();
    } catch (err) {
      fail(err);
    }
    setChecked([]);
    await refresh();
  };

  return (
    <Stack maw={860}>
      <Group>
        <TextInput placeholder="Search" value={search} onChange={(e) => setSearch(e.currentTarget.value)} w={260} />
        {editor && checked.length === 2 && (
          <Button
            variant="light"
            onClick={() => {
              setKeep(String(checked[0]));
              setMerging(true);
            }}
          >
            Merge the two selected
          </Button>
        )}
        {editor && kind === "tag" && checked.length > 0 && (
          <Group gap="xs">
            <Autocomplete
              placeholder="Merge selected into…"
              value={tagTarget}
              onChange={setTagTarget}
              data={(rows.data ?? []).map((r) => r.name)}
              limit={30}
              w={220}
            />
            <Button
              variant="light"
              disabled={!tagTarget.trim()}
              onClick={() => act(async () => unwrap(await api.POST("/api/tags/merge", { body: { ids: checked, target: tagTarget } })))}
            >
              Merge tags
            </Button>
          </Group>
        )}
        {editor && kind === "tag" && (unused.data ?? 0) > 0 && (
          <Button variant="subtle" color="red" onClick={() => act(async () => unwrap(await api.DELETE("/api/tags/unused")))}>
            Delete {unused.data} unused tag(s)
          </Button>
        )}
      </Group>
      <Card withBorder p={0}>
        <ScrollArea h="60vh">
          <Table fz="sm" highlightOnHover stickyHeader verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                {editor && <Table.Th w={36} />}
                <Table.Th>Name</Table.Th>
                {kind === "subcollection" && <Table.Th>Collection</Table.Th>}
                <Table.Th w={90}>Models</Table.Th>
                {editor && <Table.Th w={80} />}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {list.map((r) => (
                <Table.Tr key={r.id} data-testid="lookup-row">
                  {editor && (
                    <Table.Td>
                      <Checkbox checked={checked.includes(r.id)} onChange={() => toggle(r.id)} aria-label={`Select ${r.name}`} />
                    </Table.Td>
                  )}
                  <Table.Td>{r.name}</Table.Td>
                  {kind === "subcollection" && <Table.Td c="dimmed">{r.collection}</Table.Td>}
                  <Table.Td>{r.models}</Table.Td>
                  {editor && (
                    <Table.Td>
                      <Group gap={4} wrap="nowrap">
                        <ActionIcon
                          variant="subtle"
                          aria-label={`Rename ${r.name}`}
                          onClick={() => {
                            setRenaming(r);
                            setRenameTo(r.name);
                          }}
                        >
                          <IconEdit size={16} />
                        </ActionIcon>
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          aria-label={`Delete ${r.name}`}
                          onClick={async () => {
                            try {
                              const info = unwrap(await api.GET("/api/lookups/{kind}/{id}", { params: { path: { kind, id: r.id } } }));
                              setDeleting({ row: r, info });
                            } catch (err) {
                              fail(err);
                            }
                          }}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  )}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Card>
      {editor && (
        <Group align="flex-end">
          <TextInput label="New name" value={newName} onChange={(e) => setNewName(e.currentTarget.value)} data-testid="lookup-new-name" />
          {kind === "subcollection" && (
            <Select label="In collection" data={(collections.data ?? []).map((c) => c.name)} value={newParent} onChange={setNewParent} searchable />
          )}
          <Button
            disabled={!newName.trim() || (kind === "subcollection" && !newParent)}
            onClick={() =>
              act(async () => {
                unwrap(await api.POST("/api/lookups/{kind}", { params: { path: { kind } }, body: { name: newName, collection: newParent } }));
                setNewName("");
              })
            }
          >
            Add
          </Button>
        </Group>
      )}

      <Modal opened={renaming != null} onClose={() => setRenaming(null)} title={`Rename ${renaming?.name ?? ""}`}>
        <Stack>
          <TextInput value={renameTo} onChange={(e) => setRenameTo(e.currentTarget.value)} data-autofocus />
          <Button
            onClick={() => {
              const r = renaming!;
              setRenaming(null);
              return act(async () => unwrap(await api.PATCH("/api/lookups/{kind}/{id}", { params: { path: { kind, id: r.id } }, body: { name: renameTo } })));
            }}
          >
            Rename
          </Button>
        </Stack>
      </Modal>
      <Modal opened={deleting != null} onClose={() => setDeleting(null)} title={`Delete ${deleting?.row.name ?? ""}`}>
        {deleting && (
          <Stack>
            <Text size="sm">
              {deleting.info.models} model(s) lose this value.
              {deleting.info.subcollections > 0 && ` Its ${deleting.info.subcollections} subcollection(s) are deleted too.`}
            </Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setDeleting(null)}>
                Cancel
              </Button>
              <Button
                color="red"
                data-testid="confirm-delete"
                onClick={() => {
                  const r = deleting.row;
                  setDeleting(null);
                  return act(async () => unwrap(await api.DELETE("/api/lookups/{kind}/{id}", { params: { path: { kind, id: r.id } } })));
                }}
              >
                Delete
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
      <Modal opened={merging} onClose={() => setMerging(false)} title="Merge">
        <Stack>
          <Radio.Group label="Keep" value={keep} onChange={setKeep}>
            <Stack gap="xs" mt="xs">
              {checked.map((id) => {
                const r = byId.get(id);
                return r ? <Radio key={id} value={String(id)} label={`${label(r)} — ${r.models} model(s)`} /> : null;
              })}
            </Stack>
          </Radio.Group>
          <Text size="xs" c="dimmed">
            The other one's models get the kept value, then it is deleted.
          </Text>
          <Button
            onClick={() => {
              const k = Number(keep);
              const remove = checked.find((id) => id !== k)!;
              setMerging(false);
              return act(async () => unwrap(await api.POST("/api/lookups/{kind}/merge", { params: { path: { kind } }, body: { keep: k, remove } })));
            }}
          >
            Merge
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}

function Authors() {
  const editor = hasRole(useUser(), "editor");
  const refresh = useInvalidate();
  const authors = useQuery({ queryKey: ["lookups", "authors"], queryFn: async () => unwrap(await api.GET("/api/authors")) });
  const [search, setSearch] = useState("");
  const [action, setAction] = useState<{ kind: "rename" | "merge"; name: string } | null>(null);
  const [to, setTo] = useState("");
  const [profileFor, setProfileFor] = useState<string | null>(null);
  const list = (authors.data ?? []).filter((a) => a.name.toLowerCase().includes(search.toLowerCase()));
  const change = async (body: Schemas["AuthorChange"]) => {
    try {
      unwrap(await api.POST("/api/authors", { body }));
    } catch (err) {
      fail(err);
    }
    await refresh();
  };
  return (
    <Stack maw={760}>
      <TextInput placeholder="Search" value={search} onChange={(e) => setSearch(e.currentTarget.value)} w={260} />
      <Card withBorder p={0}>
        <ScrollArea h="60vh">
          <Table fz="sm" highlightOnHover stickyHeader verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Creator</Table.Th>
                <Table.Th w={90}>Models</Table.Th>
                <Table.Th w={150} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {list.map((a) => (
                <Table.Tr key={a.name}>
                  <Table.Td>{a.name}</Table.Td>
                  <Table.Td>{a.models}</Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Tooltip label="Profile">
                        <ActionIcon variant="subtle" aria-label={`Profile of ${a.name}`} onClick={() => setProfileFor(a.name)}>
                          <IconId size={16} />
                        </ActionIcon>
                      </Tooltip>
                      {editor && (
                        <>
                          <Button size="compact-xs" variant="subtle" onClick={() => { setAction({ kind: "rename", name: a.name }); setTo(a.name); }}>
                            Rename
                          </Button>
                          <Button size="compact-xs" variant="subtle" onClick={() => { setAction({ kind: "merge", name: a.name }); setTo(""); }}>
                            Merge
                          </Button>
                          <ActionIcon variant="subtle" color="red" aria-label={`Clear ${a.name}`} onClick={() => change({ action: "delete", name: a.name })}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Card>
      <Modal opened={action != null} onClose={() => setAction(null)} title={action?.kind === "merge" ? `Merge ${action.name} into…` : `Rename ${action?.name ?? ""}`}>
        <Stack>
          {action?.kind === "merge" ? (
            <Select data={(authors.data ?? []).map((a) => a.name).filter((n) => n !== action.name)} value={to} onChange={(v) => setTo(v ?? "")} searchable />
          ) : (
            <TextInput value={to} onChange={(e) => setTo(e.currentTarget.value)} />
          )}
          <Button
            disabled={!to.trim()}
            onClick={() => {
              const a = action!;
              setAction(null);
              return change({ action: a.kind, name: a.name, to });
            }}
          >
            {action?.kind === "merge" ? "Merge" : "Rename"}
          </Button>
        </Stack>
      </Modal>
      <ProfileModal name={profileFor} onClose={() => setProfileFor(null)} editable={editor} />
    </Stack>
  );
}

const PROFILE_FIELDS: [keyof Schemas["Profile"], string][] = [
  ["full_name", "Full name"],
  ["patreon_url", "Patreon"],
  ["artstation_url", "ArtStation"],
  ["gumroad_url", "Gumroad"],
  ["myminifactory_url", "MyMiniFactory"],
];

const NO_PROFILE: Schemas["Profile"] = { full_name: null, patreon_url: null, artstation_url: null, gumroad_url: null, myminifactory_url: null };

function ProfileModal({ name, onClose, editable }: { name: string | null; onClose: () => void; editable: boolean }) {
  const [form, setForm] = useState<Schemas["Profile"]>(NO_PROFILE);
  const profile = useQuery({
    queryKey: ["lookups", "profile", name],
    queryFn: async () => {
      const r = await api.GET("/api/authors/profile", { params: { query: { name: name! } } });
      const p = r.response.status === 404 ? NO_PROFILE : unwrap(r);
      setForm(p);
      return p;
    },
    enabled: name != null,
  });
  return (
    <Modal opened={name != null} onClose={onClose} title={`Profile: ${name ?? ""}`}>
      {profile.data && (
        <Stack>
          {PROFILE_FIELDS.map(([k, label]) => (
            <TextInput key={k} label={label} value={form[k] ?? ""} disabled={!editable} onChange={(e) => setForm({ ...form, [k]: e.currentTarget.value })} />
          ))}
          {editable && (
            <Button
              onClick={async () => {
                try {
                  unwrap(await api.PUT("/api/authors/profile", { params: { query: { name: name! } }, body: form }));
                  onClose();
                } catch (err) {
                  fail(err);
                }
              }}
            >
              Save
            </Button>
          )}
        </Stack>
      )}
    </Modal>
  );
}

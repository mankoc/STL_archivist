// Merge similar: the near-duplicates of a lookup or of the creators, each pair
// merged with one click on the name to keep.
import { Button, Group, Loader, Modal, NumberInput, ScrollArea, Select, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Schemas, unwrap } from "../api/client";
import { defaultThreshold, METHODS, type Method, thresholdLabel, thresholdRange, usedFirst } from "./similar";

type Name = Schemas["SimilarName"];

interface Props {
  /** A lookup kind, or `creator`. */
  kind: string;
  /** What the entries are called, for the title ("tags"). */
  label: string;
  opened: boolean;
  onClose: () => void;
  /** After a merge: reload what shows the values. */
  onMerged: () => Promise<void>;
}

async function merge(kind: string, keep: Name, remove: Name) {
  if (kind === "creator") unwrap(await api.POST("/api/authors", { body: { action: "merge", name: remove.name, to: keep.name } }));
  else unwrap(await api.POST("/api/lookups/{kind}/merge", { params: { path: { kind } }, body: { keep: keep.id, remove: remove.id } }));
}

export function SimilarModal({ kind, label, opened, onClose, onMerged }: Props) {
  const [method, setMethod] = useState<Method>("spelling");
  const [threshold, setThreshold] = useState(defaultThreshold("spelling"));
  const [busy, setBusy] = useState(false);
  const scan = useQuery({
    queryKey: ["lookups", "similar", kind, method, threshold],
    queryFn: async () => unwrap(await api.GET("/api/lookups/{kind}/similar", { params: { path: { kind }, query: { method, threshold } } })),
    enabled: opened,
  });
  const pick = async (keep: Name, remove: Name) => {
    setBusy(true);
    try {
      await merge(kind, keep, remove);
      await onMerged();
    } catch (err) {
      notifications.show({ color: "red", message: (err as Error).message });
    }
    setBusy(false);
  };
  const pairs = scan.data?.pairs ?? [];
  const range = thresholdRange(method);
  return (
    <Modal opened={opened} onClose={onClose} title={`Merge similar ${label}`} size="xl">
      <Stack>
        <Group align="end">
          <Select
            label="Find"
            data={METHODS}
            value={method}
            allowDeselect={false}
            onChange={(v) => {
              const m = v as Method;
              setMethod(m);
              setThreshold(defaultThreshold(m));
            }}
            w={240}
          />
          {range && (
            <NumberInput
              label={thresholdLabel(method)}
              value={threshold}
              min={range[0]}
              max={range[1]}
              onChange={(v) => typeof v === "number" && setThreshold(v)}
              w={150}
            />
          )}
          {scan.isFetching && <Loader size="sm" mb={8} />}
        </Group>
        <Text size="xs" c="dimmed">
          Click the name to keep: the other one's models get it, and the other one goes.
        </Text>
        <ScrollArea h="55vh">
          {scan.isError && <Text c="red">{scan.error.message}</Text>}
          {scan.isSuccess && !pairs.length && <Text c="dimmed">Nothing similar found.</Text>}
          <Table fz="sm" verticalSpacing={4}>
            <Table.Tbody>
              {pairs.map((p) => {
                const [first, second] = usedFirst(p);
                return (
                  <Table.Tr key={`${p.a.id}|${p.a.name}|${p.b.id}|${p.b.name}`}>
                    <Table.Td>
                      <Button size="compact-sm" variant="light" disabled={busy} onClick={() => pick(first, second)}>
                        {first.name} ({first.models})
                      </Button>
                    </Table.Td>
                    <Table.Td>
                      <Button size="compact-sm" variant="default" disabled={busy} onClick={() => pick(second, first)}>
                        {second.name} ({second.models})
                      </Button>
                    </Table.Td>
                    <Table.Td c="dimmed">{p.score}</Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </ScrollArea>
        {scan.data?.truncated && (
          <Text size="xs" c="dimmed">
            Only the closest {pairs.length} pairs are shown: merge some, or narrow the search.
          </Text>
        )}
      </Stack>
    </Modal>
  );
}

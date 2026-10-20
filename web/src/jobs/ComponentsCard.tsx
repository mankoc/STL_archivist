// Maintenance → AI and add-on components (administrators): download the AI
// tagger's runtime and model, the CLIP encoder, ONNX Runtime and pdfium into the
// library's folder, or remove them.
import { Alert, Anchor, Badge, Button, Card, Checkbox, Group, SegmentedControl, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";
import { defaultVariant, installable, NEEDS_ACCEPTANCE, sizeOf, totalBytes } from "./components";
import type { Job, JobRequest } from "./jobs";

const STATE: Record<string, [string, string]> = {
  installed: ["green", "installed"],
  missing: ["gray", "not installed"],
  external: ["blue", "configured elsewhere"],
  unavailable: ["orange", "not offered here"],
};

export function ComponentsCard({ jobs, start }: { jobs: Job[] | undefined; start: (r: JobRequest) => Promise<void> }) {
  // Refresh when the newest component job changes state.
  const last = (jobs ?? [])
    .filter((j) => j.kind === "install_components" || j.kind === "remove_components")
    .reduce<Job | undefined>((a, j) => (!a || j.id > a.id ? j : a), undefined);
  const status = useQuery({
    queryKey: ["components", last?.id, last?.state],
    queryFn: async () => unwrap(await api.GET("/api/components")),
  });
  const info = status.data;
  const [variant, setVariant] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [accepted, setAccepted] = useState(false);
  useEffect(() => {
    if (info && variant == null) setVariant(defaultVariant(info));
  }, [info, variant]);
  const v = variant ?? "cpu";
  const components = useMemo(() => info?.components ?? [], [info]);
  const toInstall = chosen.filter((id) => components.some((c) => c.id === id && installable(c, v)));
  const toRemove = chosen.filter((id) => components.some((c) => c.id === id && c.state === "installed"));
  const total = totalBytes(components, chosen, v);
  const needsLicence = toInstall.some((id) => NEEDS_ACCEPTANCE.includes(id));
  const toggle = (id: string) => setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));

  if (!info) return null;
  if (!info.platform) {
    return (
      <Card withBorder>
        <Title order={5} mb="xs">AI and add-on components</Title>
        <Text size="sm" c="dimmed">There are no downloads for this server's platform; place the files by hand (see the deployment guide).</Text>
      </Card>
    );
  }
  return (
    <Card withBorder data-testid="components-card">
      <Group justify="space-between" mb="xs">
        <Title order={5}>AI and add-on components</Title>
        {info.free_bytes != null && <Text size="xs" c="dimmed">{formatSize(info.free_bytes)} free</Text>}
      </Group>
      <Text size="xs" c="dimmed" mb="xs">
        Downloaded into the library's folder, from their official sources, and checked before use.
      </Text>
      {info.variants.length > 1 && (
        <Group gap="xs" mb="xs">
          <Text size="sm">AI tagger runtime:</Text>
          <SegmentedControl size="xs" value={v} onChange={setVariant}
            data={info.variants.map((x) => ({ value: x, label: x === "cuda" ? "NVIDIA GPU (CUDA)" : "CPU" }))} />
          {info.nvidia_gpu && <Text size="xs" c="dimmed">NVIDIA GPU found</Text>}
        </Group>
      )}
      <Table fz="sm" verticalSpacing={4}>
        <Table.Tbody>
          {components.map((c) => {
            const [color, label] = STATE[c.state] ?? ["gray", c.state];
            const selectable = installable(c, v) || c.state === "installed";
            return (
              <Table.Tr key={c.id} data-testid="component-row">
                <Table.Td w={32}>
                  <Checkbox size="xs" disabled={!selectable} checked={chosen.includes(c.id)} onChange={() => toggle(c.id)} aria-label={c.label} />
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{c.label}</Text>
                  <Text size="xs" c="dimmed">
                    {c.enables} · <Anchor href={c.license_url} target="_blank" size="xs">{c.license}</Anchor>
                    {c.state !== "missing" && c.detail ? ` · ${c.detail}` : ""}
                  </Text>
                </Table.Td>
                <Table.Td w={90} ta="right">{formatSize(sizeOf(c, v))}</Table.Td>
                <Table.Td w={150}>
                  <Badge size="sm" variant="light" color={color}>
                    {label}{c.variant ? ` (${c.variant})` : ""}
                  </Badge>
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      {needsLicence && (
        <Alert mt="xs" color="yellow">
          <Checkbox size="xs" checked={accepted} onChange={(e) => setAccepted(e.currentTarget.checked)} data-testid="accept-licence"
            label={<>I accept the <Anchor href={components.find((c) => c.id === "ai_model")?.license_url} target="_blank" size="xs">Qwen Research License</Anchor> of the AI tagger model (it limits commercial use).</>} />
        </Alert>
      )}
      <Group mt="xs" gap="xs">
        <Button size="xs" disabled={!toInstall.length || (needsLicence && !accepted)} data-testid="install-components"
          onClick={() => { void start({ kind: "install_components", components: toInstall, variant: v }); setChosen([]); setAccepted(false); }}>
          Download {toInstall.length ? `(${formatSize(total)})` : ""}
        </Button>
        <Button size="xs" variant="light" color="red" disabled={!toRemove.length}
          onClick={() => { void start({ kind: "remove_components", components: toRemove }); setChosen([]); }}>
          Remove selected
        </Button>
      </Group>
    </Card>
  );
}

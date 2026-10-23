// Maintenance → AI and add-on components (administrators): download the AI
// tagger's runtime and model, the CLIP encoder, ONNX Runtime and pdfium into the
// library's folder, or remove them.
import { Card, Group, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "../api/client";
import { formatSize } from "../format";
import { ComponentsTable, GpuLine } from "./ComponentsTable";
import type { Job, JobRequest } from "./jobs";

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
      <GpuLine info={info} />
      <ComponentsTable
        info={info}
        busy={last?.state === "queued" || last?.state === "running"}
        onInstall={(components, variants) => void start({ kind: "install_components", components, variants })}
        onRemove={(components) => void start({ kind: "remove_components", components })}
      />
    </Card>
  );
}

// Maintenance → Compute workers: the machines that run CLIP embeddings and the
// AI tagger for this server, and — in the desktop app — the switch that makes
// this computer one of them.
import { Alert, Badge, Card, Code, Group, Stack, Switch, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { inDesktopShell, startWorker, stopWorker, workerState } from "../desktop";
import { modeText, workerSummary } from "./workers";

export function WorkersCard({ editor }: { editor: boolean }) {
  const queryClient = useQueryClient();
  const desktop = inDesktopShell();
  const status = useQuery({
    queryKey: ["workers"],
    queryFn: async () => unwrap(await api.GET("/api/workers")),
    refetchInterval: 5000,
  });
  const local = useQuery({ queryKey: ["worker-state"], queryFn: workerState, enabled: desktop, refetchInterval: 3000 });
  const [busy, setBusy] = useState(false);

  const toggle = async (on: boolean) => {
    setBusy(true);
    try {
      const state = on ? await startWorker(unwrap(await api.POST("/api/workers/token")).token) : await stopWorker();
      queryClient.setQueryData(["worker-state"], state);
      void queryClient.invalidateQueries({ queryKey: ["workers"] });
    } catch (err) {
      notifications.show({ color: "red", title: "Compute worker", message: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const workers = status.data?.workers ?? [];
  const me = local.data;
  return (
    <Card withBorder data-testid="workers-card">
      <Title order={5} mb="xs">Compute workers</Title>
      {status.data && <Text size="xs" c="dimmed" mb="xs">{modeText(status.data.compute)}</Text>}
      <Stack gap={6}>
        {workers.length === 0 && <Text size="sm" c="dimmed">No worker is connected.</Text>}
        {workers.map((w) => (
          <div key={w.id} data-testid="worker-row">
            <Group gap="xs">
              <Text size="sm" fw={500}>{w.name}</Text>
              <Badge size="xs" variant="light">{w.user}</Badge>
            </Group>
            <Text size="xs" c="dimmed">{workerSummary(w)}</Text>
            {w.note && <Text size="xs" c="orange">{w.note}</Text>}
          </div>
        ))}
      </Stack>
      {desktop && editor && (
        <Stack gap={4} mt="sm">
          <Switch
            label="Use this computer for AI"
            description="Embeddings and AI tags for this server run on this computer's GPU while the desktop app is open."
            checked={!!me?.running}
            disabled={busy || !me}
            onChange={(e) => void toggle(e.currentTarget.checked)}
            data-testid="worker-switch"
          />
          {me && (
            <Text size="xs" c="dimmed">
              Uses the components installed in <Code>{me.data_dir}</Code> (the local library's folder; install them from its Maintenance page).
            </Text>
          )}
          {me?.other_server && <Text size="xs" c="dimmed">This computer works for {me.other_server}; turning this on moves it here.</Text>}
          {me?.stopped && (
            <Alert color="red" p="xs">
              <Text size="xs">The worker stopped: {me.stopped}</Text>
            </Alert>
          )}
        </Stack>
      )}
    </Card>
  );
}

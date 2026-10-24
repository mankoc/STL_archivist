// Maintenance → Compute workers: the machines that run CLIP embeddings and the
// AI tagger for this server, and — in the desktop app — the switch that makes
// this computer one of them. The components it computes with are downloaded
// in Settings → Components.
import { Alert, Anchor, Badge, Card, Divider, Group, Stack, Switch, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { inDesktopShell, startWorker, stopWorker, workerState } from "../desktop";
import { HelpLink } from "../help/HelpPage";
import { modeText, workerSummary } from "./workers";

const fail = (err: unknown) => notifications.show({ color: "red", title: "Compute worker", message: (err as Error).message });

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
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const workers = status.data?.workers ?? [];
  const me = local.data;
  return (
    <Card withBorder data-testid="workers-card">
      <Group gap={4} mb="xs">
        <Title order={5}>Compute workers</Title>
        <HelpLink topic="compute-workers" label="Help: compute workers" />
      </Group>
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
      {desktop && (
        <>
          <Divider my="sm" label="This computer" labelPosition="left" />
          {editor && (
            <Stack gap={4} mb="sm">
              <Switch
                label="Use this computer for AI"
                description="Embeddings and AI tags for this server run on this computer while the desktop app is open, with the components below."
                checked={!!me?.running}
                disabled={busy || !me}
                onChange={(e) => void toggle(e.currentTarget.checked)}
                data-testid="worker-switch"
              />
              {me?.other_server && <Text size="xs" c="dimmed">This computer works for {me.other_server}; turning this on moves it here.</Text>}
              {me?.stopped && (
                <Alert color="red" p="xs">
                  <Text size="xs">The worker stopped: {me.stopped}</Text>
                </Alert>
              )}
            </Stack>
          )}
          <Text size="xs" c="dimmed">
            The components this computer computes with are downloaded in{" "}
            <Anchor href="#/settings/components" size="xs">Settings → Components</Anchor>.
          </Text>
        </>
      )}
    </Card>
  );
}

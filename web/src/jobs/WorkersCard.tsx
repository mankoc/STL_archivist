// Maintenance → Compute workers: the machines that run CLIP embeddings and the
// AI tagger for this server, and — in the desktop app — the switch that makes
// this computer one of them, with the components it computes with (downloaded
// into a folder of this computer, chosen here).
import { Alert, Badge, Button, Card, Code, Divider, Group, Progress, Stack, Switch, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import {
  changeWorkerComponents,
  inDesktopShell,
  pickWorkerFolder,
  startWorker,
  stopWorker,
  workerComponents,
  workerInstallState,
  workerState,
} from "../desktop";
import { formatSize } from "../format";
import { HelpLink } from "../help/HelpPage";
import { ComponentsTable, GpuLine } from "./ComponentsTable";
import { installProgress, modeText, workerSummary } from "./workers";

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
          <LocalComponents />
        </>
      )}
    </Card>
  );
}

/** The components in the worker's folder on this computer. */
function LocalComponents() {
  const queryClient = useQueryClient();
  const install = useQuery({
    queryKey: ["worker-install"],
    queryFn: workerInstallState,
    refetchInterval: (q) => (q.state.data?.running ? 1000 : false),
  });
  const running = !!install.data?.running;
  const comps = useQuery({ queryKey: ["worker-components"], queryFn: workerComponents });
  // Read the folder again when an install or removal ends.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) {
      void queryClient.invalidateQueries({ queryKey: ["worker-components"] });
      void queryClient.invalidateQueries({ queryKey: ["worker-state"] });
    }
    wasRunning.current = running;
  }, [running, queryClient]);

  const change = async (action: "install" | "remove", components: string[], variants?: Record<string, string>) => {
    try {
      queryClient.setQueryData(["worker-install"], await changeWorkerComponents(action, components, variants));
    } catch (err) {
      fail(err);
    }
  };
  const pick = async () => {
    try {
      if (await pickWorkerFolder()) {
        void queryClient.invalidateQueries({ queryKey: ["worker-components"] });
        void queryClient.invalidateQueries({ queryKey: ["worker-state"] });
      }
    } catch (err) {
      fail(err);
    }
  };

  const data = comps.data;
  const progress = install.data ? installProgress(install.data) : null;
  return (
    <Stack gap={4} data-testid="local-components">
      <Group justify="space-between" wrap="nowrap">
        <Text size="xs" c="dimmed">
          Components in <Code>{data?.data_dir ?? "…"}</Code>
          {data?.info.free_bytes != null ? ` · ${formatSize(data.info.free_bytes)} free` : ""}
        </Text>
        <Button size="compact-xs" variant="default" disabled={running} onClick={() => void pick()} data-testid="pick-worker-folder">
          Change folder…
        </Button>
      </Group>
      {comps.error && <Text size="xs" c="red">{(comps.error as Error).message}</Text>}
      {data && (
        <>
          <GpuLine info={data.info} />
          <ComponentsTable
            key={data.data_dir}
            info={data.info}
            busy={running}
            onInstall={(components, variants) => void change("install", components, variants)}
            onRemove={(components) => void change("remove", components)}
          />
        </>
      )}
      {progress?.text && (
        <Stack gap={2} mt={4}>
          {progress.percent != null && <Progress value={progress.percent} size="sm" />}
          <Text size="xs" c={install.data?.error ? "red" : "dimmed"}>{progress.text}</Text>
        </Stack>
      )}
    </Stack>
  );
}

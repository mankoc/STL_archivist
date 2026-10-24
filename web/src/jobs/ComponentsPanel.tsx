// Settings → Components: one place for every download. The server's components
// (administrators: the AI tagger, CLIP, ONNX Runtime, pdfium, UnRAR, in the
// library's folder) and, in the desktop app, those of this computer's compute
// worker — unless the worker uses the same folder (a local library), when the
// server's list already is the worker's.
import { Alert, Button, Card, Code, Group, Progress, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { changeWorkerComponents, inDesktopShell, pickWorkerFolder, workerComponents, workerInstallState } from "../desktop";
import { formatSize } from "../format";
import { HelpLink } from "../help/HelpPage";
import { ComponentsCard } from "./ComponentsCard";
import { ComponentsTable, GpuLine } from "./ComponentsTable";
import { sameFolder } from "./components";
import { addJob, type Job, type JobRequest } from "./jobs";
import { JobLog } from "./JobsPage";
import { installProgress } from "./workers";

const fail = (err: unknown) => notifications.show({ color: "red", title: "Components", message: (err as Error).message });

export function ComponentsPanel({ admin }: { admin: boolean }) {
  const queryClient = useQueryClient();
  const desktop = inDesktopShell();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")), enabled: admin });
  const server = useQuery({ queryKey: ["components-dir"], queryFn: async () => unwrap(await api.GET("/api/components")).data_dir, enabled: admin });
  const local = useQuery({ queryKey: ["worker-components"], queryFn: workerComponents, enabled: desktop });
  const [last, setLast] = useState<number | null>(null);
  const start = async (request: JobRequest) => {
    try {
      const job = unwrap(await api.POST("/api/jobs", { body: request }));
      queryClient.setQueryData<Job[]>(["jobs"], (old) => addJob(old, job));
      setLast(job.id);
    } catch (err) {
      fail(err);
    }
  };
  const current = (jobs.data ?? []).find((j) => j.id === last);
  const shared = sameFolder(server.data, local.data?.data_dir);

  return (
    <Stack gap="md" maw={980}>
      {admin && <ComponentsCard jobs={jobs.data} start={start} />}
      {current && <JobLog id={current.id} job={current} />}
      {desktop && (
        <Card withBorder data-testid="local-components-card">
          <Group gap={4} mb="xs">
            <Title order={5}>This computer (compute worker)</Title>
            <HelpLink topic="compute-workers" label="Help: compute workers" />
          </Group>
          {shared ? (
            <Text size="sm" c="dimmed" data-testid="worker-shares-folder">
              The compute worker on this computer uses the library's folder (<Code>{local.data?.data_dir}</Code>): the components
              above serve it too. Change its folder to keep separate ones.
              <Button size="compact-xs" variant="default" ml="xs" onClick={() => void pickFolder(queryClient)}>Change folder…</Button>
            </Text>
          ) : (
            <LocalComponents />
          )}
        </Card>
      )}
      {!admin && !desktop && <Text size="sm" c="dimmed">Only administrators manage the server's components.</Text>}
    </Stack>
  );
}

async function pickFolder(queryClient: ReturnType<typeof useQueryClient>) {
  try {
    if (await pickWorkerFolder()) {
      void queryClient.invalidateQueries({ queryKey: ["worker-components"] });
      void queryClient.invalidateQueries({ queryKey: ["worker-state"] });
    }
  } catch (err) {
    fail(err);
  }
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

  const data = comps.data;
  const progress = install.data ? installProgress(install.data) : null;
  return (
    <Stack gap={4} data-testid="local-components">
      <Group justify="space-between" wrap="nowrap">
        <Text size="xs" c="dimmed">
          Components in <Code>{data?.data_dir ?? "…"}</Code>
          {data?.info.free_bytes != null ? ` · ${formatSize(data.info.free_bytes)} free` : ""}
        </Text>
        <Button size="compact-xs" variant="default" disabled={running} onClick={() => void pickFolder(queryClient)} data-testid="pick-worker-folder">
          Change folder…
        </Button>
      </Group>
      {comps.error && <Alert color="red" p="xs">{(comps.error as Error).message}</Alert>}
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

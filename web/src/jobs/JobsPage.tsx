// Crawls (the desktop's Crawler): crawled folders, the job queue, and a live
// log. Database Maintenance has its own page (MaintenancePage).
import {
  ActionIcon, Badge, Box, Button, Card, Checkbox, Group, Modal, Progress, ScrollArea, Stack,
  Table, Text, TextInput, Title, Tooltip, UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconEye, IconEyeOff, IconFolderPlus, IconPlayerPlay, IconPlayerStop, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { type ServerEvent, useServerEvent } from "../api/events";
import { hasRole, useUser } from "../auth/user";
import { FolderPicker } from "./FolderPicker";
import { describe, elapsed, isActive, type Job, type JobRequest, percent, progressText, sortJobs, STATE_COLOR } from "./jobs";
import classes from "./JobsPage.module.css";

async function submit(request: JobRequest) {
  try {
    const job = unwrap(await api.POST("/api/jobs", { body: request }));
    notifications.show({ message: `Queued: ${describe(job)}` });
    return job;
  } catch (err) {
    notifications.show({ color: "red", title: "Could not start", message: (err as Error).message });
    return null;
  }
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export function JobsPage() {
  const user = useUser();
  const editor = hasRole(user, "editor");
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
  const folders = useQuery({ queryKey: ["crawled-folders"], queryFn: async () => unwrap(await api.GET("/api/crawled-folders")) });
  const [selected, setSelected] = useState<number | null>(null);
  const [picker, setPicker] = useState(false);
  const [crawl, setCrawl] = useState<{ folder: string; pattern: string; force: boolean } | null>(null);
  const list = sortJobs(jobs.data ?? []);
  const now = useNow(list.some(isActive));
  const shown = selected ?? list[0]?.id ?? null;

  const refreshFolders = () => queryClient.invalidateQueries({ queryKey: ["crawled-folders"] });
  const start = async (request: JobRequest) => {
    const job = await submit(request);
    if (job) setSelected(job.id);
  };

  return (
    <div className={classes.layout}>
      <Stack gap="md" className={classes.column}>
        <Card withBorder>
          <Group justify="space-between" mb="xs">
            <Title order={5}>Crawled folders</Title>
            {editor && (
              <Group gap="xs">
                <Button size="xs" variant="light" leftSection={<IconFolderPlus size={14} />} onClick={() => setPicker(true)}>
                  Add folder
                </Button>
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconPlayerPlay size={14} />}
                  disabled={!folders.data?.length}
                  onClick={async () => {
                    const job = await submit({ kind: "update_folders" });
                    if (job) setSelected(job.id);
                  }}
                >
                  Update all
                </Button>
              </Group>
            )}
          </Group>
          <Table fz="sm" highlightOnHover>
            <Table.Tbody>
              {(folders.data ?? []).map((f) => (
                <Table.Tr key={f.path}>
                  <Table.Td style={{ wordBreak: "break-all" }}>
                    {f.path}
                    {f.pattern && (
                      <Text size="xs" c="dimmed">
                        {f.pattern}
                      </Text>
                    )}
                    {f.monitored && (
                      <Badge size="xs" variant="light" ml={0} data-testid="monitored">
                        monitored
                      </Badge>
                    )}
                  </Table.Td>
                  {editor && (
                    <Table.Td w={120}>
                      <Group gap={4} wrap="nowrap">
                        <Tooltip label={f.monitored ? "Stop monitoring" : "Monitor: check for new or changed archives and crawl automatically"}>
                          <ActionIcon
                            variant={f.monitored ? "light" : "subtle"}
                            aria-label={f.monitored ? "Stop monitoring" : "Monitor"}
                            onClick={async () => {
                              try {
                                unwrap(await api.POST("/api/crawled-folders/monitor", { body: { path: f.path, monitored: !f.monitored } }));
                              } catch (err) {
                                notifications.show({ color: "red", title: "Monitor", message: (err as Error).message });
                              }
                              await refreshFolders();
                            }}
                          >
                            {f.monitored ? <IconEye size={16} /> : <IconEyeOff size={16} />}
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Crawl">
                          <ActionIcon variant="subtle" onClick={() => setCrawl({ folder: f.path, pattern: f.pattern ?? "", force: false })} aria-label="Crawl">
                            <IconPlayerPlay size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Stop tracking (its models stay)">
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            aria-label="Stop tracking"
                            onClick={async () => {
                              await api.POST("/api/crawled-folders/remove", { body: { path: f.path } });
                              await refreshFolders();
                            }}
                          >
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  )}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {folders.data?.length === 0 && (
            <Text size="sm" c="dimmed">
              No folders yet{editor ? ": add one to crawl it." : "."}
            </Text>
          )}
        </Card>

      </Stack>

      <Stack gap="md" className={classes.column}>
        <Card withBorder className={classes.jobs}>
          <Title order={5} mb="xs">
            Jobs
          </Title>
          <ScrollArea.Autosize mah={280}>
            {list.map((job) => {
              const p = percent(job);
              return (
                <UnstyledButton
                  key={job.id}
                  className={classes.job}
                  data-selected={job.id === shown || undefined}
                  onClick={() => setSelected(job.id)}
                  data-testid="job"
                >
                  <Group justify="space-between" wrap="nowrap">
                    <Text size="sm" truncate flex={1}>
                      {describe(job)}
                    </Text>
                    <Badge color={STATE_COLOR[job.state]} variant="light" size="sm">
                      {job.state}
                    </Badge>
                    {editor && isActive(job) && (
                      <ActionIcon
                        size="sm"
                        variant="subtle"
                        color="red"
                        aria-label="Cancel"
                        onClick={async (e) => {
                          e.stopPropagation();
                          await api.DELETE("/api/jobs/{id}", { params: { path: { id: job.id } } });
                        }}
                      >
                        <IconPlayerStop size={14} />
                      </ActionIcon>
                    )}
                  </Group>
                  {job.state === "running" && <Progress value={p ?? 100} striped={p === null} animated={p === null} size="sm" mt={4} />}
                  <Text size="xs" c="dimmed" truncate>
                    {[job.phase && job.state === "running" ? job.phase : job.message, progressText(job, now), elapsed(job, now), job.user].filter(Boolean).join(" · ")}
                  </Text>
                </UnstyledButton>
              );
            })}
            {list.length === 0 && (
              <Text size="sm" c="dimmed">
                No jobs yet.
              </Text>
            )}
          </ScrollArea.Autosize>
        </Card>
        {shown != null && <JobLog id={shown} job={list.find((j) => j.id === shown)} />}
      </Stack>

      <FolderPicker
        opened={picker}
        onClose={() => setPicker(false)}
        onPick={async (path) => {
          setPicker(false);
          try {
            unwrap(await api.POST("/api/crawled-folders", { body: { path } }));
          } catch (err) {
            notifications.show({ color: "red", message: (err as Error).message });
          }
          await refreshFolders();
          setCrawl({ folder: path, pattern: "", force: false });
        }}
      />
      <Modal opened={crawl != null} onClose={() => setCrawl(null)} title="Crawl folder">
        {crawl && (
          <Stack>
            <Text size="sm" style={{ wordBreak: "break-all" }}>
              {crawl.folder}
            </Text>
            <TextInput
              label="Path pattern"
              description="For example {sculptor}/{release}/{title}; empty to take values from the archives only"
              value={crawl.pattern}
              onChange={(e) => setCrawl({ ...crawl, pattern: e.currentTarget.value })}
            />
            <Checkbox label="Re-extract archives whose content did not change" checked={crawl.force} onChange={(e) => setCrawl({ ...crawl, force: e.currentTarget.checked })} />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setCrawl(null)}>
                Cancel
              </Button>
              <Button
                data-testid="start-crawl"
                onClick={async () => {
                  const c = crawl;
                  setCrawl(null);
                  await start({ kind: "crawl", folder: c.folder, pattern: c.pattern, force_rescan: c.force });
                  await refreshFolders();
                }}
              >
                Start
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </div>
  );
}

/** A job's log: what the server kept when it was opened, then new lines as they arrive. */
export function JobLog({ id, job }: { id: number; job: Job | undefined }) {
  const detail = useQuery({
    queryKey: ["job-log", id],
    queryFn: async () => unwrap(await api.GET("/api/jobs/{id}", { params: { path: { id } } })).log,
    staleTime: Infinity,
  });
  const [extra, setExtra] = useState<string[]>([]);
  const viewport = useRef<HTMLDivElement>(null);
  useEffect(() => setExtra([]), [id]);
  const onEvent = useCallback(
    (e: ServerEvent) => {
      if (e.type === "job_log" && e.id === id) setExtra((x) => [...x.slice(-5000), e.line]);
    },
    [id],
  );
  useServerEvent(onEvent);
  const lines = [...(detail.data ?? []), ...extra];
  useEffect(() => {
    viewport.current?.scrollTo({ top: viewport.current.scrollHeight });
  }, [lines.length]);
  return (
    <Card withBorder className={classes.logCard}>
      <Group justify="space-between" mb="xs">
        <Title order={5}>Log</Title>
        {job && (
          <Text size="xs" c="dimmed" truncate maw="70%">
            {describe(job)}
          </Text>
        )}
      </Group>
      <ScrollArea viewportRef={viewport} className={classes.log} data-testid="job-log">
        <Box component="pre" m={0} fz="xs" ff="monospace">
          {lines.join("\n")}
          {job?.message && !isActive(job) ? `\n\n${job.message}` : ""}
        </Box>
      </ScrollArea>
    </Card>
  );
}

// Jobs: the job queue and a live log. Libraries (crawls) and Database
// Maintenance have their own places (LibrariesPage, Settings → Maintenance).
import { ActionIcon, Badge, Box, Card, Group, Progress, ScrollArea, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { IconPlayerStop } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, unwrap } from "../api/client";
import { type ServerEvent, useServerEvent } from "../api/events";
import { hasRole, useUser } from "../auth/user";
import { describe, elapsed, isActive, type Job, percent, progressText, sortJobs, STATE_COLOR } from "./jobs";
import classes from "./JobsPage.module.css";

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
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
  const [selected, setSelected] = useState<number | null>(null);
  const list = sortJobs(jobs.data ?? []);
  const now = useNow(list.some(isActive));
  const shown = selected ?? list[0]?.id ?? null;

  return (
    <div className={classes.layout}>
      <Stack gap="md" className={classes.column}>
        <Card withBorder className={classes.jobs}>
          <Title order={5} mb="xs">
            Jobs
          </Title>
          <ScrollArea.Autosize mah="calc(100vh - 160px)">
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
      </Stack>

      <Stack gap="md" className={classes.column}>
        {shown != null && <JobLog id={shown} job={list.find((j) => j.id === shown)} />}
      </Stack>

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

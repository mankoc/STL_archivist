// The status bar at the bottom of every page: the running job (click: the Jobs
// page) and how many archives the libraries hold. Both lists stay current
// through the server's events (`api/events.ts`).
import { Box, Group, Progress, Text, UnstyledButton } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, unwrap } from "../api/client";
import { status, totalsText } from "./status";

export const STATUS_BAR_HEIGHT = 26;

/** Seconds since the epoch, every second while `active` (for the time left). */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export function StatusBar() {
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
  const libs = useQuery({ queryKey: ["libraries"], queryFn: async () => unwrap(await api.GET("/api/libraries")) });
  const running = (jobs.data ?? []).some((j) => j.state === "running");
  const s = status(jobs.data, libs.data, useNow(running));

  return (
    <Group h={STATUS_BAR_HEIGHT} px="md" gap="md" justify="space-between" wrap="nowrap" data-testid="status-bar">
      <UnstyledButton onClick={() => (location.hash = "#/jobs")} style={{ minWidth: 0, flex: 1 }} aria-label="Jobs">
        <Group gap="xs" wrap="nowrap">
          {s.job ? (
            <>
              <Box w={120} style={{ flexShrink: 0 }}>
                <Progress size="sm" value={s.job.percent ?? 100} striped={s.job.percent == null} animated={s.job.percent == null} />
              </Box>
              <Text size="xs" truncate>
                <b>{s.job.label}</b>
                {s.job.detail ? ` · ${s.job.detail}` : ""}
              </Text>
            </>
          ) : (
            <Text size="xs" c="dimmed">No job running</Text>
          )}
          {s.queued > 0 && (
            <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
              {s.queued} queued
            </Text>
          )}
        </Group>
      </UnstyledButton>
      <Text size="xs" c="dimmed" style={{ flexShrink: 0 }} data-testid="status-totals">
        {totalsText(s)}
      </Text>
    </Group>
  );
}

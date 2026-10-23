// Libraries: the libraries and their folders (LibrariesCard), the crawl dialog
// their folders open, and the log of the last job started here (the Jobs page
// has every job).
import { Anchor, Button, Card, Checkbox, Group, Modal, Stack, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "../api/client";
import { hasRole, useUser } from "../auth/user";
import { addJob, type Job, type JobRequest } from "./jobs";
import { JobLog } from "./JobsPage";
import classes from "./JobsPage.module.css";
import { LibrariesCard } from "./LibrariesCard";

export function LibrariesPage() {
  const user = useUser();
  const editor = hasRole(user, "editor");
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/jobs")) });
  const [last, setLast] = useState<number | null>(null);
  const [crawl, setCrawl] = useState<{ folder: string; pattern: string; force: boolean } | null>(null);

  const start = async (request: JobRequest) => {
    try {
      const job = unwrap(await api.POST("/api/jobs", { body: request }));
      queryClient.setQueryData<Job[]>(["jobs"], (old) => addJob(old, job));
      setLast(job.id);
    } catch (err) {
      notifications.show({ color: "red", title: "Could not start", message: (err as Error).message });
    }
  };
  const current = (jobs.data ?? []).find((j) => j.id === last);

  return (
    <div className={classes.layout}>
      <Stack gap="md" className={classes.column}>
        <LibrariesCard editor={editor} onCrawl={(folder, pattern) => setCrawl({ folder, pattern, force: false })} start={start} />
      </Stack>

      <Stack gap="md" className={classes.column}>
        {last != null ? (
          <JobLog id={last} job={current} />
        ) : (
          <Card withBorder>
            <Title order={5} mb="xs">Log</Title>
            <Text size="sm" c="dimmed">
              Crawls started here show their log here; every job is on the <Anchor href="#/jobs">Jobs</Anchor> page.
            </Text>
          </Card>
        )}
      </Stack>

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
                  await queryClient.invalidateQueries({ queryKey: ["libraries"] });
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

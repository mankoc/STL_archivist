// Settings tabs that were cards of the Maintenance page: sidecars, compute
// workers, and backups (with Delete all records). Each shows the log of the
// last job started on it.
import { Stack } from "@mantine/core";

import { BackupCard, DangerCard, SidecarCard } from "./BackupCards";
import { JobLog } from "./JobsPage";
import { useStartJob } from "./useStartJob";
import { WorkersCard } from "./WorkersCard";

export function SidecarsTab({ editor }: { editor: boolean }) {
  const { start, current } = useStartJob();
  return (
    <Stack gap="md" maw={980}>
      <SidecarCard editor={editor} start={start} />
      {current && <JobLog id={current.id} job={current} />}
    </Stack>
  );
}

export function WorkersTab({ editor }: { editor: boolean }) {
  return (
    <Stack gap="md" maw={980}>
      <WorkersCard editor={editor} />
    </Stack>
  );
}

/** Administrators only. */
export function BackupsTab() {
  const { jobs, start, current } = useStartJob();
  return (
    <Stack gap="md" maw={980}>
      <BackupCard jobs={jobs} start={start} />
      <DangerCard start={start} />
      {current && <JobLog id={current.id} job={current} />}
    </Stack>
  );
}

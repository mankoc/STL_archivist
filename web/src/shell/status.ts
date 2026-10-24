// The status bar's content: the running job and the size of the libraries.
import { describe, type Job, percent, progressText, sortJobs } from "../jobs/jobs";

export interface Status {
  /** The running job, if any. */
  job: { label: string; detail: string | null; percent: number | null } | null;
  /** Jobs waiting behind it. */
  queued: number;
  /** Archives catalogued in all libraries (null until known). */
  archives: number | null;
  libraries: number;
}

/** What the status bar shows, from the job list and the libraries' record counts. */
export function status(jobs: Job[] | undefined, libraries: { models: number }[] | undefined, now: number): Status {
  const list = sortJobs(jobs ?? []);
  const running = list.find((j) => j.state === "running");
  return {
    job: running
      ? {
          label: describe(running),
          detail: [running.phase, progressText(running, now)].filter(Boolean).join(" · ") || null,
          percent: percent(running),
        }
      : null,
    queued: list.filter((j) => j.state === "queued").length,
    archives: libraries ? libraries.reduce((n, l) => n + l.models, 0) : null,
    libraries: libraries?.length ?? 0,
  };
}

/** "12,345 archives in 3 libraries". */
export function totalsText(s: Status): string {
  if (s.archives == null) return "";
  const archives = `${s.archives.toLocaleString()} archive${s.archives === 1 ? "" : "s"}`;
  return `${archives} in ${s.libraries} librar${s.libraries === 1 ? "y" : "ies"}`;
}

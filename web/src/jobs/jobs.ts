// Job display helpers.
import type { Schemas } from "../api/client";

export type Job = Schemas["JobInfo"];
export type JobRequest = Schemas["JobRequest"];

export const ACTIVE: Job["state"][] = ["queued", "running"];

export function isActive(job: Job): boolean {
  return ACTIVE.includes(job.state);
}

/** Percent done, or null while the total is unknown. */
export function percent(job: Job): number | null {
  if (job.total <= 0) return job.state === "done" ? 100 : null;
  return Math.min(100, Math.round((job.done / job.total) * 100));
}

export const STATE_COLOR: Record<Job["state"], string> = {
  queued: "gray",
  running: "blue",
  done: "green",
  failed: "red",
  cancelled: "orange",
};

const KIND_LABEL: Record<string, string> = {
  crawl: "Crawl",
  verify_archives: "Verify archives",
  recompute_dhashes: "Recompute image hashes",
  prune_images: "Prune images",
  compact_database: "Compact database",
  compute_embeddings: "Compute embeddings",
  find_content_duplicates: "Find duplicate STL files",
  find_image_duplicates: "Find duplicate images",
  find_metadata_duplicates: "Find duplicate metadata",
  find_duplicate_entries: "Find duplicate entries",
  find_moved: "Find moved in DB",
  search_folder: "Search in folder",
  recheck_warnings: "Re-check warnings",
  generate_sidecars: "Generate sidecars",
  remove_sidecars: "Remove sidecars",
  backup: "Backup",
  ai_suggest: "AI suggestion",
  ai_tag: "AI tagging",
};

/** "Crawl D:/STL/Patreon", "Verify archives", … */
export function describe(job: Job): string {
  const label = KIND_LABEL[job.kind] ?? job.kind;
  const r = job.request as { folder?: string; all?: boolean; keep?: number; warning?: string };
  if (r.folder) return `${label} ${r.folder}`;
  if (r.warning) return `${label} (${r.warning === "no_images" ? "images" : "presupport"})`;
  if (r.keep !== undefined) return `${label} (keep ${r.keep})`;
  if (r.all) return `${label} (all)`;
  return label;
}

/** Seconds as "45 s", "3 min 05 s", "2 h 07 min". */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}

/** How long a job ran (or has been running). */
export function elapsed(job: Job, now: number): string | null {
  if (job.started_at == null) return null;
  return duration((job.finished_at ?? now) - job.started_at);
}

/** Newest first, with active jobs on top. */
export function sortJobs(jobs: Job[]): Job[] {
  return [...jobs].sort((a, b) => Number(isActive(b)) - Number(isActive(a)) || b.id - a.id);
}

/** Add a job just submitted to a cached list, unless its events got there
 *  first (a quick job can finish before the submit response arrives). */
export function addJob(old: Job[] | undefined, job: Job): Job[] {
  const list = old ?? [];
  return list.some((j) => j.id === job.id) ? list : [...list, job];
}

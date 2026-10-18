// Duplicate finders: which job's result to show, and how to label it.
import type { Schemas } from "../api/client";

export type Job = Schemas["JobInfo"];
export type JobResult = Schemas["JobResult"];
export type Finder = "content" | "images" | "metadata";

export const FINDER_KIND: Record<Finder, string> = {
  content: "find_content_duplicates",
  images: "find_image_duplicates",
  metadata: "find_metadata_duplicates",
};

export const METADATA_COLUMNS = [
  ["title", "Title"], ["creator", "Creator"], ["source", "Source"], ["sculptor", "Sculptor"], ["collection", "Collection"],
  ["subcollection", "Subcollection"], ["category", "Category"], ["release", "Release"], ["scale", "Scale"],
].map(([value, label]) => ({ value, label }));

/** The newest job of a finder: the one whose progress or result is shown. */
export function latestJob(jobs: Job[] | undefined, finder: Finder): Job | undefined {
  return (jobs ?? []).filter((j) => j.kind === FINDER_KIND[finder]).reduce<Job | undefined>((a, j) => (!a || j.id > a.id ? j : a), undefined);
}

/** "3 bits differ" / "similarity 0.962" / "100 %". */
export function scoreLabel(method: string, best: number): string {
  return method === "dhash" ? (best === 0 ? "identical" : best === 1 ? "1 bit differs" : `${best} bits differ`) : `similarity ${best.toFixed(3)}`;
}

export function percentLabel(fraction: number): string {
  return `${Math.round(fraction * 1000) / 10} %`;
}

/** Rows whose titles or paths contain every word of `text` (case-insensitive). */
export function matches(text: string, ...fields: (string | null | undefined)[]): boolean {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = fields.map((f) => f ?? "").join("\n").toLowerCase();
  return words.every((w) => hay.includes(w));
}

export const PAGE_SIZE = 100;

export function pageCount(n: number, size = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(n / size));
}

/** Page `page` (1-based) of `rows`, clamped to the last page. */
export function pageOf<T>(rows: T[], page: number, size = PAGE_SIZE): T[] {
  const p = Math.min(Math.max(1, page), pageCount(rows.length, size));
  return rows.slice((p - 1) * size, p * size);
}

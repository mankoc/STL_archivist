// The detail pane's view logic: which facts it lists, how, and the tab it opens on.
import { formatDate, formatSize } from "../format";

export type Fields = Record<string, string | number | null | undefined>;

/** The pane's tabs; Images first, and the one it opens on. */
export const TABS = ["images", "details", "files"] as const;
export type Tab = (typeof TABS)[number];
const TAB_KEY = "stl.detailTab";

/** The tab chosen last in this browser (a convenience: Images when none). */
export function loadTab(): Tab {
  try {
    const t = localStorage.getItem(TAB_KEY);
    return (TABS as readonly string[]).includes(t ?? "") ? (t as Tab) : "images";
  } catch {
    return "images";
  }
}

export function saveTab(tab: Tab): void {
  try {
    localStorage.setItem(TAB_KEY, tab);
  } catch {
    /* a convenience only */
  }
}

const text = (v: unknown) => (v == null ? "" : String(v).trim());

/** The facts the Details tab lists, as `[label, value]`; those without a value are left out. */
export function detailFacts(f: Fields): [string, string][] {
  const collection = [text(f.collection_name), text(f.subcollection_name)].filter(Boolean).join(" › ");
  const rate = typeof f.compression_rate === "number" && f.compression_rate > 0 ? ` (${Math.round(f.compression_rate * 100)} %)` : "";
  const files = typeof f.file_count === "number" && f.file_count > 0 ? `${f.file_count} · ${f.stl_count ?? 0} STL` : "";
  const facts: [string, string][] = [
    ["Creator", text(f.creator)],
    ["Type", text(f.source)],
    ["Sculptor", text(f.sculptor_name)],
    ["Collection", collection],
    ["Category", text(f.category_name)],
    ["Release", text(f.release_name)],
    ["Date", f.release_year != null ? formatDate(f.release_year as number, (f.release_month as number | null) ?? null) : ""],
    ["Scales", text(f.scales)],
    ["Sequence", text(f.sequence)],
    ["Size", f.archive_size != null ? formatSize(f.archive_size as number) + rate : ""],
    ["Files", files],
    ["Presupports", text(f.presupports) === "No" ? "" : text(f.presupports)],
    ["Added", text(f.created_at).slice(0, 10)],
  ];
  return facts.filter(([, v]) => v !== "");
}

/** A long path shortened in the middle, so that the archive's name stays whole. */
export function middlePath(path: string, max = 64): string {
  if (path.length <= max) return path;
  const name = path.split(/[\\/]/).pop() ?? "";
  const head = Math.max(8, max - name.length - 1);
  return name.length + 9 >= max ? `…${path.slice(-(max - 1))}` : `${path.slice(0, head)}…${path.slice(path.length - name.length - 1)}`;
}

/** `images` in the order of `ids` (the server's rule: those not listed follow, as they were). */
export function orderedImages<T extends { id: number }>(images: T[], ids: number[]): T[] {
  const place = new Map(ids.map((id, i) => [id, i]));
  return images
    .map((img, i) => ({ img, key: place.get(img.id) ?? ids.length + i }))
    .sort((a, b) => a.key - b.key)
    .map((x) => x.img);
}

/** `ids` with `id` moved to where `target` is (before it moving up, after it moving down). */
export function moveImage(ids: number[], id: number, target: number): number[] {
  const from = ids.indexOf(id);
  const to = ids.indexOf(target);
  if (from < 0 || to < 0 || from === to) return ids;
  const out = ids.filter((i) => i !== id);
  out.splice(to, 0, id);
  return out;
}

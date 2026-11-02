// Import from a web page: the review of what a model's page says, and the
// request that applies what the review kept (WebImportModal.tsx).
import type { Schemas } from "../api/client";

export type WebPage = Schemas["WebPageInfo"];
export type WebImportBody = Schemas["WebImport"];
export type WebImported = Schemas["WebImported"];
type EditorModel = Schemas["EditorModel"];

/** The fields a page fills, in the dialog's order. */
export const WEB_FIELDS = [
  { key: "title", label: "Title" },
  { key: "creator", label: "Creator" },
  { key: "source", label: "Source" },
  { key: "url", label: "URL" },
  { key: "description", label: "Description" },
] as const;
export type WebField = (typeof WEB_FIELDS)[number]["key"];

export interface FieldChoice {
  value: string;
  apply: boolean;
}

export interface Review {
  fields: Record<WebField, FieldChoice>;
  /** Tags the model does not have yet. */
  tags: { tag: string; apply: boolean }[];
  images: { url: string; apply: boolean }[];
  /** Skip images that look like one the model has. */
  skipKnown: boolean;
}

/** The address typed, with `https://` when it has no scheme; null unless http(s). */
export function pageUrl(text: string): string | null {
  const t = text.trim();
  if (!t) return null;
  try {
    const u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(t) ? t : `https://${t}`);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

/** Sites that answer only a browser (bot checks) or show posts only to a signed-in one:
 *  the desktop app reads them in its window straight away. */
const BROWSER_SITES = ["cults3d.com", "patreon.com", "thingiverse.com", "printables.com", "cgtrader.com"];

export function needsBrowser(url: string): boolean {
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/^www\./, "");
    return BROWSER_SITES.some((d) => host === d || host.endsWith(`.${d}`));
  } catch {
    return false;
  }
}

/** The review to start from: the page's values ticked where the model's field is
 *  empty, every new tag, the first `maxImages` images. */
export function startReview(model: EditorModel, page: WebPage, maxImages: number): Review {
  const found: Record<WebField, string | null | undefined> = {
    title: page.title,
    creator: page.creator,
    source: page.source,
    url: page.url,
    description: page.description,
  };
  const fields = {} as Record<WebField, FieldChoice>;
  for (const { key } of WEB_FIELDS) {
    const value = (found[key] ?? "").trim();
    fields[key] = { value, apply: value !== "" && !(model[key] ?? "").trim() };
  }
  const have = new Set(model.tags.map((t) => t.toLowerCase()));
  return {
    fields,
    tags: page.tags.filter((t) => !have.has(t.toLowerCase())).map((tag) => ({ tag, apply: true })),
    images: page.images.map((url, i) => ({ url, apply: i < maxImages })),
    skipKnown: true,
  };
}

/** The request: ticked fields with a value (the rest null: left as they are), tags and images. */
export function importBody(r: Review): WebImportBody {
  const value = (key: WebField) => (r.fields[key].apply && r.fields[key].value.trim()) || null;
  return {
    title: value("title"),
    creator: value("creator"),
    source: value("source"),
    url: value("url"),
    description: value("description"),
    tags: r.tags.filter((t) => t.apply).map((t) => t.tag),
    images: r.images.filter((i) => i.apply).map((i) => i.url),
    skip_known: r.skipKnown,
  };
}

/** What the review would change: fields, tags and images. */
export function changeCount(r: Review): number {
  const b = importBody(r);
  return WEB_FIELDS.filter(({ key }) => b[key] != null).length + b.tags.length + b.images.length;
}

/** "Fields and tags updated, 2 images added (1 already there)", for the notification. */
export function importSummary(r: WebImported): string {
  const parts: string[] = [];
  if (r.changed) parts.push("fields and tags updated");
  if (r.added.length) parts.push(`${r.added.length} image(s) added`);
  const notes: string[] = [];
  if (r.skipped) notes.push(`${r.skipped} already there`);
  if (r.failed.length) notes.push(`${r.failed.length} could not be downloaded`);
  const head = parts.length ? parts.join(", ") : "Nothing new: the model already had it";
  const text = head.charAt(0).toUpperCase() + head.slice(1);
  return notes.length ? `${text} (${notes.join(", ")}).` : `${text}.`;
}

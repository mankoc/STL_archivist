// The mass editor's form and the request it becomes (`POST /api/models/mass-edit`),
// following the desktop dialog: typed manual fields apply, cleared ones become
// null, find & replace only with a find text and target fields.
import type { Schemas } from "../api/client";

export type MassEdit = Schemas["MassEdit"];

export const MANUAL = [
  ["title", "Title"],
  ["creator", "Creator"],
  ["source", "Type"],
  ["release_date", "Date"],
  ["url", "URL"],
] as const;
export type ManualField = (typeof MANUAL)[number][0];

export const FIND_FIELDS = ["title", "creator", "source", "url"] as const;

export interface MassForm {
  pattern: string;
  overwrite: boolean;
  manual: Record<ManualField, string>;
  clear: Record<ManualField, boolean>;
  scale: string;
  /** "" = no change, "(clear)" = clear, else the value. */
  presupports: string;
  release: string;
  collection: string;
  subcollection: string;
  category: string;
  sculptor: string;
  tags: string;
  find: string;
  replace: string;
  findCase: boolean;
  findRegex: boolean;
  findFields: string[];
  trim: boolean;
}

export const CLEAR_PRESUPPORTS = "(clear)";

export function emptyForm(): MassForm {
  const blank = Object.fromEntries(MANUAL.map(([k]) => [k, ""])) as Record<ManualField, string>;
  const off = Object.fromEntries(MANUAL.map(([k]) => [k, false])) as Record<ManualField, boolean>;
  return {
    pattern: "", overwrite: false, manual: blank, clear: off, scale: "", presupports: "", release: "",
    collection: "", subcollection: "", category: "", sculptor: "", tags: "", find: "", replace: "",
    findCase: false, findRegex: false, findFields: [], trim: false,
  };
}

export function toRequest(form: MassForm, ids: number[]): MassEdit {
  const manual: Record<string, string | null> = {};
  for (const [k] of MANUAL) {
    if (form.clear[k]) manual[k] = null;
    else if (form.manual[k].trim()) manual[k] = form.manual[k].trim();
  }
  const find =
    form.find.trim() && form.findFields.length
      ? { find: form.find, replace: form.replace, case: form.findCase, regex: form.findRegex, fields: form.findFields }
      : null;
  return {
    ids,
    pattern: form.pattern.trim(),
    overwrite: form.overwrite,
    manual,
    clearing: MANUAL.filter(([k]) => form.clear[k]).map(([k]) => k),
    scale: form.scale.trim(),
    presupports: form.presupports === "" ? null : form.presupports === CLEAR_PRESUPPORTS ? "" : form.presupports,
    release: form.release.trim(),
    collection: form.collection.trim(),
    subcollection: form.subcollection.trim(),
    category: form.category.trim(),
    sculptor: form.sculptor.trim(),
    tags: form.tags,
    find,
    trim: form.trim,
  };
}

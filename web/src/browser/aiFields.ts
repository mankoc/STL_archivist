// "AI data" in the model editor: the fields the model inferred, merged into the form.
import type { Schemas } from "../api/client";

export type EditorForm = Required<Schemas["EditorSave"]>;
export type AiEditorFields = Schemas["AiEditorFields"];

const TEXT: [keyof EditorForm & keyof AiEditorFields, string][] = [
  ["title", "Title"], ["creator", "Creator"], ["source", "Type"], ["date", "Date"], ["sculptor", "Sculptor"],
  ["release", "Release"], ["collection", "Collection"], ["subcollection", "Subcollection"], ["category", "Category"],
  ["description", "Description"],
];

/** Values not in `have` (compared without case). */
function missing(have: string[], add: string[]): string[] {
  const seen = new Set(have.map((v) => v.toLowerCase()));
  return add.filter((v) => v && !seen.has(v.toLowerCase()));
}

/** The form with empty fields filled from the model's answer, scales and tags
 *  added; and the labels of what changed. Filled fields are kept. */
export function mergeAiFields(form: EditorForm, ai: AiEditorFields): { form: EditorForm; filled: string[] } {
  const out = { ...form };
  const filled: string[] = [];
  for (const [key, label] of TEXT) {
    const v = (ai[key] as string).trim();
    if (v && !(out[key] as string).trim()) {
      (out[key] as string) = v;
      filled.push(label);
    }
  }
  for (const [key, label] of [["scales", "Scales"], ["tags", "Tags"]] as const) {
    const add = missing(out[key], ai[key]);
    if (add.length) {
      out[key] = [...out[key], ...add];
      filled.push(label);
    }
  }
  return { form: out, filled };
}

/** The editor's form for a model as the server returns it. */
export function formOf(m: Schemas["EditorModel"]): EditorForm {
  return {
    title: m.title ?? "", creator: m.creator ?? "", source: m.source ?? "", date: m.date ?? "",
    presupports: m.presupports ?? "", url: m.url ?? "", description: m.description ?? "",
    sequence: m.sequence ?? 0, release: m.release ?? "", sculptor: m.sculptor ?? "",
    collection: m.collection ?? "", subcollection: m.subcollection ?? "", category: m.category ?? "",
    scales: m.scales, tags: m.tags,
  };
}

export type AiIssue = Schemas["AiIssue"];

/** The AI check's field names as editor form keys (`Scale` and `Year` handled apart). */
const ISSUE_FIELD: Record<string, keyof EditorForm> = {
  Title: "title", Creator: "creator", Sculptor: "sculptor", Category: "category", Collection: "collection",
  Subcollection: "subcollection", Source: "source", Description: "description",
};

/** Whether a problem's suggestion can be applied (a known field and a suggested value). */
export function canApply(issue: AiIssue): boolean {
  const v = issue.suggestion.trim();
  if (!v) return false;
  if (issue.field === "Year") return /^\d{4}$/.test(v);
  return issue.field === "Scale" || issue.field in ISSUE_FIELD;
}

/** The form with the problems' suggestions applied: a field takes the suggestion,
 *  scales are replaced by the suggested list, a year keeps the date's month. */
export function applyIssues(form: EditorForm, issues: AiIssue[]): EditorForm {
  const out = { ...form };
  for (const i of issues.filter(canApply)) {
    const v = i.suggestion.trim();
    if (i.field === "Scale") out.scales = v.split(",").map((s) => s.trim()).filter(Boolean);
    else if (i.field === "Year") out.date = /^\d{4}-\d{2}$/.test(out.date) ? `${v}${out.date.slice(4)}` : v;
    else (out[ISSUE_FIELD[i.field]] as string) = v;
  }
  return out;
}

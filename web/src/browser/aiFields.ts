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

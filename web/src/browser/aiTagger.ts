// AI tagger drawer helpers: the editable suggestion and its tag list.
import type { Schemas } from "../api/client";

export type Suggestion = Schemas["AiSuggestion"];
export type Fields = Required<Omit<Schemas["AiFieldValues"], "year">> & { year: number | null };

export const SOURCES = ["patreon", "kickstarter", "myminifactory"];

export const EMPTY_FIELDS: Fields = {
  title: "", creator: "", sculptor: "", collection: "", subcollection: "", scale: "", description: "", source: "", year: null,
};

/** The suggestion's fields with every key present. */
export function fieldsOf(s: Suggestion | null | undefined): Fields {
  const f = s?.fields ?? {};
  return {
    title: f.title ?? "", creator: f.creator ?? "", sculptor: f.sculptor ?? "", collection: f.collection ?? "",
    subcollection: f.subcollection ?? "", scale: f.scale ?? "", description: f.description ?? "", source: f.source ?? "",
    year: f.year ?? null,
  };
}

/** Add a tag (trimmed) unless it is empty or already there in any case. */
export function addTag(tags: string[], tag: string): string[] {
  const t = tag.trim();
  if (!t || tags.some((x) => x.toLowerCase() === t.toLowerCase())) return tags;
  return [...tags, t];
}

export function removeTag(tags: string[], tag: string): string[] {
  return tags.filter((t) => t !== tag);
}

/** The newest `ai_suggest` job for a model. */
export function suggestJob(jobs: Schemas["JobInfo"][] | undefined, modelId: number): Schemas["JobInfo"] | undefined {
  return (jobs ?? [])
    .filter((j) => j.kind === "ai_suggest" && (j.request as { id?: number }).id === modelId)
    .reduce<Schemas["JobInfo"] | undefined>((a, j) => (!a || j.id > a.id ? j : a), undefined);
}

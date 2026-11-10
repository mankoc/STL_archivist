// OpenRouter's free vision models, suggested for the AI tagger's model. The
// list is OpenRouter's public catalogue (it allows browser requests); the
// server only needs the chosen slug, which may be any OpenRouter model.
export const OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models";

type Catalogue = { data?: { id: string; architecture?: { input_modalities?: string[] } }[] };

/** The slugs of free models that read images, sorted. */
export function freeVisionModels(catalogue: Catalogue): string[] {
  return (catalogue.data ?? [])
    .filter((m) => m.id.endsWith(":free") && (m.architecture?.input_modalities ?? []).includes("image"))
    .map((m) => m.id)
    .sort();
}

export async function fetchFreeVisionModels(): Promise<string[]> {
  const r = await fetch(OPENROUTER_MODELS_URL);
  if (!r.ok) throw new Error(`OpenRouter: HTTP ${r.status}`);
  return freeVisionModels(await r.json());
}

/** A pasted model slug, or the slug of a pasted openrouter.ai model page. */
export function modelSlug(text: string): string {
  const t = text.trim();
  const page = /^(?:https?:\/\/)?(?:www\.)?openrouter\.ai\/(?:models\/)?([^/?#\s]+\/[^/?#\s]+)/.exec(t);
  return page ? page[1] : t;
}

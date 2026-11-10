// OpenRouter's free vision models, for the AI tagger's model picker. The list
// is OpenRouter's public catalogue (it allows browser requests); the server
// only needs the chosen id.
export const OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models";

type Catalogue = { data?: { id: string; name?: string; architecture?: { input_modalities?: string[] } }[] };

/** Free models that read images, as Select options sorted by name. */
export function freeVisionModels(catalogue: Catalogue): { value: string; label: string }[] {
  return (catalogue.data ?? [])
    .filter((m) => m.id.endsWith(":free") && (m.architecture?.input_modalities ?? []).includes("image"))
    .map((m) => ({ value: m.id, label: m.name ?? m.id }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export async function fetchFreeVisionModels(): Promise<{ value: string; label: string }[]> {
  const r = await fetch(OPENROUTER_MODELS_URL);
  if (!r.ok) throw new Error(`OpenRouter: HTTP ${r.status}`);
  return freeVisionModels(await r.json());
}

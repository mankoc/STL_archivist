// Merge similar's choices (the view is SimilarModal.tsx).
import type { Schemas } from "../api/client";

export type Method = "spelling" | "sound" | "words";

export const METHODS: { value: Method; label: string }[] = [
  { value: "spelling", label: "Similar spelling" },
  { value: "sound", label: "Sounds alike" },
  { value: "words", label: "Shared words" },
];

export function defaultThreshold(method: Method): number {
  return method === "words" ? 80 : 2;
}

/** The least and most the threshold may be; `null` when the method has none. */
export function thresholdRange(method: Method): [number, number] | null {
  if (method === "sound") return null;
  return method === "words" ? [1, 100] : [0, 5];
}

export function thresholdLabel(method: Method): string {
  return method === "words" ? "Words shared (%)" : "Letters that differ";
}

/** The pair's names, the one more models use first: the likely one to keep. */
export function usedFirst(pair: Schemas["SimilarPair"]): [Schemas["SimilarName"], Schemas["SimilarName"]] {
  return pair.b.models > pair.a.models ? [pair.b, pair.a] : [pair.a, pair.b];
}

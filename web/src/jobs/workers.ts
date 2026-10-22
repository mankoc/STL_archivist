// Compute workers (Maintenance → Compute workers): how the server's mode and a
// connected worker are described.
import type { Schemas } from "../api/client";

export type WorkerInfo = Schemas["WorkerInfo"];

/** What the server does without a worker. */
export function modeText(compute: string): string {
  return compute === "workers"
    ? "This server runs no AI itself: embedding and tagging jobs wait for a worker, and crawls skip embeddings while none is connected."
    : "Embeddings and the AI tagger run on a connected worker when there is one, else on this server (when it has the components).";
}

/** What a worker does for this server. */
export function workerSummary(w: WorkerInfo): string {
  const parts: string[] = [];
  if (w.clip_provider) parts.push(`CLIP on ${w.clip_provider}`);
  if (w.chat_model) parts.push(`AI tagger (${w.chat_model})`);
  if (!parts.length) parts.push("nothing this server can use");
  parts.push(`${w.done} request${w.done === 1 ? "" : "s"} answered${w.busy ? `, ${w.busy} running` : ""}`);
  return parts.join(" · ");
}

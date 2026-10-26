// Sync with other servers (Settings → Backups & transfer): what a pull or a
// push will replace, the folder rules typed in, a preview's summary and the
// last sync, as text.
import type { Schemas } from "../api/client";

export type RemoteInfo = Schemas["RemoteInfo"];
export type SyncPreview = Schemas["SyncPreview"];
export type PathRule = Schemas["PathRule"];
export type Direction = "pull" | "push";

/** What a sync with `name` replaces, for the confirmation. */
export function syncWarning(direction: Direction, name: string): string {
  return direction === "pull"
    ? `This server's library will be replaced by ${name}'s. Edits made here since the last sync are lost.`
    : `${name}'s library will be replaced by this server's. Edits made on ${name} since the last sync are lost.`;
}

/** A rule row as typed (either side may still be empty). */
export interface RuleRow {
  local: string;
  remote: string;
}

/** The rules to save (blank rows dropped), or why they cannot be saved. */
export function cleanRules(rows: RuleRow[]): { rules: PathRule[]; error: string | null } {
  const rules: PathRule[] = [];
  for (const r of rows) {
    const [local, remote] = [r.local.trim(), r.remote.trim()];
    if (!local && !remote) continue;
    if (!local || !remote) return { rules, error: "Each rule needs a folder on both servers." };
    rules.push({ local, remote });
  }
  return { rules, error: null };
}

/** How a preview's folders land on the receiving side. */
export function previewSummary(p: SyncPreview): string {
  const n = p.folders.length;
  const unmapped = p.folders.filter((f) => !f.target).length;
  const absent = p.folders.filter((f) => f.target && !f.exists).length;
  const parts = [`${n} folder${n === 1 ? "" : "s"}`];
  if (unmapped) parts.push(`${unmapped} not mapped by a rule (its records would show as missing)`);
  if (absent) parts.push(`${absent} mapped to a folder that does not exist there`);
  if (!unmapped && !absent) parts.push("all mapped to existing folders");
  return parts.join(", ");
}

/** The last sync with a remote (`when` formats Unix seconds), or `null`. */
export function lastSyncText(r: RemoteInfo, when: (unix: number) => string): string | null {
  if (r.last_sync_at == null) return null;
  const verb = r.last_direction === "push" ? "Pushed" : "Pulled";
  return `${verb} ${when(r.last_sync_at)}: ${r.last_result ?? ""}`.trim();
}

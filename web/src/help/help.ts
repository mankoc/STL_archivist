// Help page logic: inline text formatting, search and the topic in the URL.
import { type Block, type Topic, TOPICS } from "./topics";

export type Segment =
  | { kind: "text"; text: string }
  | { kind: "bold"; text: string }
  | { kind: "code"; text: string }
  | { kind: "link"; text: string; topic: string };

/** Split help text into plain, **bold**, `code` and [label](topic) pieces. */
export function inline(text: string): Segment[] {
  const out: Segment[] = [];
  const re = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([a-z0-9-]+)\)/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push({ kind: "text", text: text.slice(last, m.index) });
    if (m[1] != null) out.push({ kind: "bold", text: m[1] });
    else if (m[2] != null) out.push({ kind: "code", text: m[2] });
    else out.push({ kind: "link", text: m[3], topic: m[4] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

/** A block's text without markup (for search). */
function blockText(b: Block): string {
  if ("p" in b) return b.p;
  if ("h" in b) return b.h;
  if ("tip" in b) return b.tip;
  if ("warn" in b) return b.warn;
  if ("list" in b) return b.list.join(" ");
  if ("steps" in b) return b.steps.join(" ");
  if ("img" in b) return "";
  return [...b.table.head, ...b.table.rows.flat()].join(" ");
}

const plain = (s: string) => inline(s).map((x) => x.text).join("").toLowerCase();

export function topicText(t: Topic): string {
  return plain([t.title, t.summary, ...t.blocks.map(blockText)].join(" "));
}

/** Topics containing every word of `query`, those matching in the title first. */
export function searchTopics(query: string, topics: Topic[] = TOPICS): Topic[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return topics;
  const hits = topics.filter((t) => {
    const text = topicText(t);
    return words.every((w) => text.includes(w));
  });
  const inTitle = (t: Topic) => words.some((w) => t.title.toLowerCase().includes(w));
  return [...hits.filter(inTitle), ...hits.filter((t) => !inTitle(t))];
}

/** The topic named after `#/help/` (the first one when none or unknown). */
export function topicFromHash(hash: string, topics: Topic[] = TOPICS): Topic {
  const id = hash.replace(/^#\/?/, "").split("/")[1] ?? "";
  return topics.find((t) => t.id === id) ?? topics[0];
}

export const topicHash = (id: string) => `#/help/${id}`;

/** Every link in the help points at a topic that exists. */
export function brokenLinks(topics: Topic[] = TOPICS): string[] {
  const ids = new Set(topics.map((t) => t.id));
  return topics.flatMap((t) =>
    t.blocks
      .flatMap((b) => ("list" in b ? b.list : "steps" in b ? b.steps : "table" in b ? b.table.rows.flat() : [blockText(b)]))
      .flatMap(inline)
      .filter((s) => s.kind === "link" && !ids.has(s.topic))
      .map((s) => `${t.id} → ${(s as { topic: string }).topic}`),
  );
}

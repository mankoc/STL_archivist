import { brokenLinks, inline, searchTopics, topicFromHash } from "./help";
import { GROUPS, TOPICS } from "./topics";

describe("help", () => {
  it("formats bold, code and topic links", () => {
    expect(inline("Open **Jobs**, use `{title}` or see [patterns](path-patterns).")).toEqual([
      { kind: "text", text: "Open " },
      { kind: "bold", text: "Jobs" },
      { kind: "text", text: ", use " },
      { kind: "code", text: "{title}" },
      { kind: "text", text: " or see " },
      { kind: "link", text: "patterns", topic: "path-patterns" },
      { kind: "text", text: "." },
    ]);
    expect(inline("plain")).toEqual([{ kind: "text", text: "plain" }]);
  });

  it("finds topics by every word, title matches first", () => {
    const ids = searchTopics("monitored").map((t) => t.id);
    expect(ids[0]).toBe("monitored-folders");
    expect(ids).toContain("jobs");
    expect(searchTopics("directml worker").map((t) => t.id)).toContain("compute-workers");
    expect(searchTopics("nothing-like-this")).toEqual([]);
    expect(searchTopics("  ")).toHaveLength(TOPICS.length);
  });

  it("reads the topic from the address", () => {
    expect(topicFromHash("#/help/embeddings").id).toBe("embeddings");
    expect(topicFromHash("#/help").id).toBe(TOPICS[0].id);
    expect(topicFromHash("#/help/unknown").id).toBe(TOPICS[0].id);
  });

  it("is consistent: unique ids, known groups, working links", () => {
    expect(new Set(TOPICS.map((t) => t.id)).size).toBe(TOPICS.length);
    expect(TOPICS.every((t) => (GROUPS as readonly string[]).includes(t.group))).toBe(true);
    expect(brokenLinks()).toEqual([]);
  });
});

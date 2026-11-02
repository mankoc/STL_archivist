import { describe, expect, it } from "vitest";

import type { Schemas } from "../api/client";
import { changeCount, importBody, importSummary, needsBrowser, pageUrl, startReview, type WebPage } from "./webImport";

const model = {
  id: 1, title: "Ettercap", creator: null, source: "", url: null, description: null,
  tags: ["Spider"], scales: [], images: [], date: "", to_print: false,
} as unknown as Schemas["EditorModel"];

const page: WebPage = {
  url: "https://www.myminifactory.com/object/3d-print-278136",
  title: "Ettercap Hunter",
  creator: "YKSMiniatures",
  source: "MyMiniFactory",
  description: "A humanoid spider.",
  tags: ["spider", "monster", "dnd"],
  images: ["https://a/1.jpg", "https://a/2.jpg", "https://a/3.jpg"],
};

describe("web import", () => {
  it("reads typed addresses", () => {
    expect(pageUrl("  cults3d.com/en/3d-model/x ")).toBe("https://cults3d.com/en/3d-model/x");
    expect(pageUrl("http://nas:8080/a")).toBe("http://nas:8080/a");
    expect(pageUrl("")).toBeNull();
    expect(pageUrl("ftp://x.org/a")).toBeNull();
    expect(pageUrl("javascript:alert(1)")).toBeNull();
  });

  it("knows the sites only a browser reads", () => {
    expect(needsBrowser("https://www.patreon.com/posts/1")).toBe(true);
    expect(needsBrowser("https://cults3d.com/en/x")).toBe(true);
    expect(needsBrowser("https://www.myminifactory.com/object/x")).toBe(false);
    expect(needsBrowser("https://notpatreon.com/")).toBe(false);
    expect(needsBrowser("nonsense")).toBe(false);
  });

  it("starts by filling empty fields with new tags and the first images", () => {
    const r = startReview(model, page, 2);
    expect(r.fields.title).toEqual({ value: "Ettercap Hunter", apply: false });
    expect(r.fields.creator).toEqual({ value: "YKSMiniatures", apply: true });
    expect(r.fields.source.apply).toBe(true);
    expect(r.fields.url.apply).toBe(true);
    expect(r.tags).toEqual([{ tag: "monster", apply: true }, { tag: "dnd", apply: true }]);
    expect(r.images.map((i) => i.apply)).toEqual([true, true, false]);
    expect(r.skipKnown).toBe(true);
    expect(startReview(model, { ...page, title: null, images: [] }, 2).fields.title).toEqual({ value: "", apply: false });
  });

  it("sends what is ticked", () => {
    const r = startReview(model, page, 1);
    r.fields.title = { value: " Ettercap Hunter (Pre-Supported) ", apply: true };
    r.fields.description.apply = false;
    r.tags[1].apply = false;
    expect(importBody(r)).toEqual({
      title: "Ettercap Hunter (Pre-Supported)",
      creator: "YKSMiniatures",
      source: "MyMiniFactory",
      url: "https://www.myminifactory.com/object/3d-print-278136",
      description: null,
      tags: ["monster"],
      images: ["https://a/1.jpg"],
      skip_known: true,
    });
    expect(changeCount(r)).toBe(6);
    r.fields.creator = { value: "  ", apply: true };
    expect(importBody(r).creator).toBeNull();
  });

  it("sums the import up", () => {
    const added = [{ id: 7, thumbnail: null }, { id: 8, thumbnail: null }] as Schemas["WebImported"]["added"];
    expect(importSummary({ changed: true, added, skipped: 1, failed: [] })).toBe("Fields and tags updated, 2 image(s) added (1 already there).");
    expect(importSummary({ changed: false, added: [], skipped: 2, failed: ["x: 404"] })).toBe(
      "Nothing new: the model already had it (2 already there, 1 could not be downloaded).",
    );
    expect(importSummary({ changed: true, added: [], skipped: 0, failed: [] })).toBe("Fields and tags updated.");
  });
});

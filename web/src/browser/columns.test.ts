import { choiceFrom, idsFrom, toggle, visible } from "./columns";

const cols = [{ id: "title" }, { id: "creator" }, { id: "path" }, { id: "url", optional: true }];
const none = { hidden: [], shown: [] };

describe("columns", () => {
  it("reads the stored settings", () => {
    expect(idsFrom(["path", 3, "title"])).toEqual(["path"]);
    expect(idsFrom("path")).toEqual([]);
    expect(choiceFrom({ table_hidden_columns: ["path"], table_shown_columns: ["url"] })).toEqual({ hidden: ["path"], shown: ["url"] });
    expect(choiceFrom(undefined)).toEqual(none);
  });

  it("shows standard columns until hidden and optional ones once turned on", () => {
    expect(visible(cols, none).map((c) => c.id)).toEqual(["title", "creator", "path"]);
    expect(visible(cols, { hidden: ["path", "title"], shown: ["url"] }).map((c) => c.id)).toEqual(["title", "creator", "url"]);
  });

  it("toggles", () => {
    expect(toggle(none, cols[2])).toEqual({ hidden: ["path"], shown: [] });
    expect(toggle({ hidden: ["path"], shown: [] }, cols[2])).toEqual(none);
    expect(toggle(none, cols[3])).toEqual({ hidden: [], shown: ["url"] });
    expect(toggle(none, cols[0])).toEqual(none);
  });
});

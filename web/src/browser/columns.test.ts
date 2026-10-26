import { choiceFrom, idsFrom, moveColumn, ordered, toggle, visible } from "./columns";

const cols = [{ id: "title" }, { id: "creator" }, { id: "path" }, { id: "url", optional: true }];
const none = { hidden: [], shown: [], order: [] };

describe("columns", () => {
  it("reads the stored settings", () => {
    expect(idsFrom(["path", 3, "title"])).toEqual(["path"]);
    expect(idsFrom("path")).toEqual([]);
    expect(choiceFrom({ table_hidden_columns: ["path"], table_shown_columns: ["url"] })).toEqual({ hidden: ["path"], shown: ["url"], order: [] });
    expect(choiceFrom({ table_column_order: ["path", 2, "title"] }).order).toEqual(["path", "title"]);
    expect(choiceFrom(undefined)).toEqual(none);
  });

  it("shows standard columns until hidden and optional ones once turned on", () => {
    expect(visible(cols, none).map((c) => c.id)).toEqual(["title", "creator", "path"]);
    expect(visible(cols, { hidden: ["path", "title"], shown: ["url"], order: [] }).map((c) => c.id)).toEqual(["title", "creator", "url"]);
    expect(visible(cols, { hidden: [], shown: [], order: ["path", "title"] }).map((c) => c.id)).toEqual(["path", "title", "creator"]);
  });

  it("toggles", () => {
    expect(toggle(none, cols[2])).toEqual({ hidden: ["path"], shown: [], order: [] });
    expect(toggle({ hidden: ["path"], shown: [], order: [] }, cols[2])).toEqual(none);
    expect(toggle(none, cols[3])).toEqual({ hidden: [], shown: ["url"], order: [] });
    expect(toggle(none, cols[0])).toEqual(none);
  });

  it("orders columns as the user dragged them", () => {
    // Named ones first, the others (unknown ids dropped) after, in their default order.
    expect(ordered(cols, ["url", "nope", "path"]).map((c) => c.id)).toEqual(["url", "path", "title", "creator"]);
    // Dragged onto a column, a column takes its place.
    expect(moveColumn(cols, [], "path", "title")).toEqual(["path", "title", "creator", "url"]);
    expect(moveColumn(cols, [], "title", "path")).toEqual(["creator", "path", "title", "url"]);
    expect(moveColumn(cols, ["url"], "creator", "url")).toEqual(["creator", "url", "title", "path"]);
    expect(moveColumn(cols, [], "path", "path")).toEqual(["title", "creator", "path", "url"]);
  });
});

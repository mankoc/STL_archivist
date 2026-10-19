import { hiddenFrom, toggle, visible } from "./columns";

const cols = [{ id: "title" }, { id: "creator" }, { id: "path" }];

describe("columns", () => {
  it("reads the stored setting", () => {
    expect(hiddenFrom(["path", 3, "title"])).toEqual(["path"]);
    expect(hiddenFrom(undefined)).toEqual([]);
    expect(hiddenFrom("path")).toEqual([]);
  });

  it("hides columns but never the title", () => {
    expect(visible(cols, ["path", "title"]).map((c) => c.id)).toEqual(["title", "creator"]);
    expect(toggle(["path"], "path")).toEqual([]);
    expect(toggle([], "creator")).toEqual(["creator"]);
    expect(toggle([], "title")).toEqual([]);
  });
});

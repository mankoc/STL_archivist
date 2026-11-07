import { folderRows, MAX_ROWS } from "./folderRows";

describe("folder rows", () => {
  const rows = [
    { folder: "Kickstarters/VintageVault", pattern: null },
    { folder: "Kickstarters", pattern: "{collection}/{title}" },
    { folder: "Patreon/Studio", pattern: null },
  ];

  it("puts the folders with a pattern of their own first, and filters", () => {
    expect(folderRows(rows, "").rows.map((r) => r.folder)).toEqual(["Kickstarters", "Kickstarters/VintageVault", "Patreon/Studio"]);
    expect(folderRows(rows, "vintage").rows.map((r) => r.folder)).toEqual(["Kickstarters/VintageVault"]);
  });

  it("shows at most so many, and says how many more there are", () => {
    const many = Array.from({ length: MAX_ROWS + 5 }, (_, i) => ({ folder: `F${i}`, pattern: null }));
    const shown = folderRows(many, "");
    expect(shown.rows).toHaveLength(MAX_ROWS);
    expect(shown.hidden).toBe(5);
  });
});

import { CARD_WIDTH, cardLayout, clampCard, columnsFor, GRID_CARD_KEY, infoLines, loadCardInfo, loadCardWidth, pagesFor, saveCardInfo, saveCardWidth } from "./grid";

describe("grid", () => {
  it("fits columns", () => {
    expect(columnsFor(0)).toBe(1);
    expect(columnsFor(180)).toBe(1);
    expect(columnsFor(370)).toBe(2);
    expect(columnsFor(1000)).toBe(5);
  });

  it("finds the pages of visible rows", () => {
    expect(pagesFor(0, 3, 5, 1000, 10)).toEqual([0, 1]);
    expect(pagesFor(10, 12, 5, 1000, 10)).toEqual([5, 6]);
    expect(pagesFor(0, 100, 5, 23, 10)).toEqual([0, 1, 2]);
    expect(pagesFor(0, 5, 5, 0)).toEqual([]);
  });

  it("shows the chosen lines below a card's title", () => {
    const cell = { id: 1, title: "Orc", creator: "Loot", archive_path: "F:/STL/Loot/Orc.7z", thumbnail: null, images: 0, archive_size: 2048, release_year: 2024, release_month: 3, created_at: "2026-11-26 11:55:01.5" };
    expect(infoLines(cell, ["added", "creator", "size"])).toEqual(["Loot", expect.stringContaining("2"), "2026-11-26 11:55"]);
    expect(infoLines(cell, ["name", "date"])).toEqual(["Orc.7z", "2024-03"]);
    expect(infoLines({ ...cell, creator: null, archive_size: undefined }, ["creator", "size"])).toEqual(["", ""]);
    expect(cardLayout(180, 3)).toEqual({ image: 150, row: 256 });
  });

  it("remembers the card lines and the grid's own width", () => {
    localStorage.clear();
    expect(loadCardInfo()).toEqual(["creator"]);
    saveCardInfo(["size", "path"]);
    expect(loadCardInfo()).toEqual(["path", "size"]);
    saveCardWidth(400, GRID_CARD_KEY);
    expect([loadCardWidth(GRID_CARD_KEY), loadCardWidth()]).toEqual([400, CARD_WIDTH]);
  });

  it("sizes cards", () => {
    expect(cardLayout(180)).toEqual({ image: 150, row: 220 });
    expect(cardLayout(360)).toEqual({ image: 300, row: 370 });
    expect([clampCard(50), clampCard(250.4), clampCard(9999)]).toEqual([120, 250, 480]);
    expect(columnsFor(1000, 360)).toBe(2);
  });

  it("remembers the card width in this browser", () => {
    localStorage.clear();
    expect(loadCardWidth()).toBe(CARD_WIDTH);
    saveCardWidth(320);
    expect(loadCardWidth()).toBe(320);
    localStorage.setItem("stl.folderCardWidth", "nonsense");
    expect(loadCardWidth()).toBe(CARD_WIDTH);
    localStorage.clear();
  });
});

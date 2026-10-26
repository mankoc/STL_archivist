import { CARD_WIDTH, cardLayout, clampCard, columnsFor, loadCardWidth, pagesFor, saveCardWidth } from "./grid";

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

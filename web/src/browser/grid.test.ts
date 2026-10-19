import { columnsFor, pagesFor } from "./grid";

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
});

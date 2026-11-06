import { clampWidth, DEFAULT_WIDTH, FILTER_PANE, loadWidth, MASS_EDIT, MIN_WIDTH, saveWidth } from "./paneWidth";

describe("detail pane width", () => {
  it("stays within limits", () => {
    expect(clampWidth(100, 1500)).toBe(MIN_WIDTH);
    expect(clampWidth(500.4, 1500)).toBe(500);
    expect(clampWidth(1200, 1500)).toBe(900);
    expect(clampWidth(500, 300)).toBe(MIN_WIDTH);
  });

  it("is remembered", () => {
    localStorage.clear();
    expect(loadWidth()).toBe(DEFAULT_WIDTH);
    saveWidth(450);
    expect(loadWidth()).toBe(450);
  });

  it("keeps the filter pane's width apart", () => {
    localStorage.clear();
    const { key, fallback, min, maxShare } = FILTER_PANE;
    expect(loadWidth(key, fallback, min)).toBe(280);
    saveWidth(420, key);
    expect(loadWidth(key, fallback, min)).toBe(420);
    expect(loadWidth()).toBe(DEFAULT_WIDTH);
    expect(clampWidth(50, 1600, maxShare, min)).toBe(200);
    expect(clampWidth(1500, 1600, maxShare, min)).toBe(800);
  });

  it("keeps the mass editor's width apart", () => {
    localStorage.clear();
    const { key, fallback, min, maxShare } = MASS_EDIT;
    expect(loadWidth(key, fallback, min)).toBe(780);
    saveWidth(300, key);
    expect(loadWidth(key, fallback, min)).toBe(780); // below its minimum
    saveWidth(1000, key);
    expect(loadWidth(key, fallback, min)).toBe(1000);
    expect(loadWidth()).toBe(DEFAULT_WIDTH);
    expect(clampWidth(300, 1600, maxShare, min)).toBe(440);
    expect(clampWidth(2000, 1600, maxShare, min)).toBe(1520);
  });
});

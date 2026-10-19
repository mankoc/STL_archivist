import { clampWidth, DEFAULT_WIDTH, loadWidth, MIN_WIDTH, saveWidth } from "./paneWidth";

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
});

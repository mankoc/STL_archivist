import { originalUrl, step } from "./ImageViewer";

describe("image viewer", () => {
  it("steps around the images", () => {
    expect(step(0, 1, 3)).toBe(1);
    expect(step(2, 1, 3)).toBe(0);
    expect(step(0, -1, 3)).toBe(2);
    expect(step(0, 1, 0)).toBe(0);
    expect(originalUrl(7)).toBe("/api/images/7/original");
  });
});

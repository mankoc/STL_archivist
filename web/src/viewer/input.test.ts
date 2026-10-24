import { canvasSize, dragAction, dragZoom, keyAction, orbitDegrees, wheelZoom } from "./input";

describe("viewer input", () => {
  it("sizes the canvas in device pixels, keeping its shape under the limit", () => {
    expect(canvasSize(800, 600, 2)).toEqual([1600, 1200]);
    expect(canvasSize(5120, 2880, 1)).toEqual([4096, 2304]);
    expect(canvasSize(3000, 1000, 2)).toEqual([4096, 1365]);
    expect(canvasSize(0, 0, 1)).toEqual([1, 1]);
  });

  it("maps buttons to MeshPeek's drags", () => {
    expect(dragAction(1, false)).toBe("orbit");
    expect(dragAction(1, true)).toBe("pan");
    expect(dragAction(4, false)).toBe("pan");
    expect(dragAction(2, false)).toBe("zoom");
    expect(dragAction(0, false)).toBe(null);
  });

  it("turns 200 degrees for a full-width drag", () => {
    expect(orbitDegrees(800, 0, 800, 600)).toEqual([-200, -0]);
    expect(orbitDegrees(0, -300, 800, 600)).toEqual([-0, 100]);
  });

  it("zooms in for upward drags and wheel turns", () => {
    expect(dragZoom(-10)).toBeGreaterThan(1);
    expect(wheelZoom(-100, 0)).toBeCloseTo(1.21);
    expect(wheelZoom(3, 1)).toBeCloseTo(wheelZoom(48, 0));
    expect(wheelZoom(100, 0)).toBeLessThan(1);
  });

  it("maps F, W and the number keys", () => {
    expect(keyAction("f")).toBe("fit");
    expect(keyAction("W")).toBe("wireframe");
    expect(keyAction("0")).toBe("isometric");
    expect(keyAction("5")).toBe("top");
    expect(keyAction("7")).toBe(null);
    expect(keyAction("Enter")).toBe(null);
  });
});

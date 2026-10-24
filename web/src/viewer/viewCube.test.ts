import { cubeFaces } from "./viewCube";

describe("view cube", () => {
  it("shows only the top face from above", () => {
    // right +X, up +Y, looking down -Z
    const faces = cubeFaces([1, 0, 0, 0, 1, 0, 0, 0, -1], 10);
    expect(faces.map((f) => f.view)).toEqual(["top"]);
    expect(faces[0].centre).toEqual([0, -0]);
    const xs = faces[0].points.map((p) => p[0]).sort((a, b) => a - b);
    expect([xs[0], xs[3]]).toEqual([-10, 10]);
  });

  it("shows three faces from the isometric view", () => {
    const f: [number, number, number] = [-1 / Math.sqrt(3), -1 / Math.sqrt(3), -1 / Math.sqrt(3)];
    const right = [-Math.SQRT1_2, Math.SQRT1_2, 0]; // f × Z
    const up = [-1 / Math.sqrt(6), -1 / Math.sqrt(6), 2 / Math.sqrt(6)]; // right × f
    const faces = cubeFaces([...right, ...up, ...f], 10);
    expect(faces.map((x) => x.view).sort()).toEqual(["back", "right", "top"]);
    // Top is drawn above the centre (screen y up is negative).
    expect(faces.find((x) => x.view === "top")!.centre[1]).toBeLessThan(0);
  });
});

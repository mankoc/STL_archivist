import { infoRows } from "./info";

describe("file information", () => {
  it("lists path, triangles, size and the file's facts", () => {
    const rows = infoRows("parts/body.stl", {
      objects: 1,
      models: 1,
      triangles: 1234567,
      support_triangles: 0,
      size: [10, 20.5, 3.125],
      facts: ["Format", "STL file", "File size", "58.9 MB"],
    });
    expect(rows).toEqual([
      ["Path", "parts/body.stl"],
      ["Triangles", "1,234,567"],
      ["Width (X)", "10.00 mm"],
      ["Depth (Y)", "20.50 mm"],
      ["Height (Z)", "3.13 mm"],
      ["Format", "STL file"],
      ["File size", "58.9 MB"],
    ]);
  });

  it("counts models and supports of a slicer scene", () => {
    const rows = infoRows("scene.lys", { objects: 6, models: 3, triangles: 10, support_triangles: 5, size: [], facts: [] });
    expect(rows).toContainEqual(["Objects", "3 models, 3 supports"]);
    expect(rows).toContainEqual(["Support triangles", "5"]);
    expect(rows.some(([label]) => label === "Width (X)")).toBe(false);
  });
});

// A shown file's information panel (MeshPeek's "Information"): its path,
// triangles, size and what the file says about itself.

/** What `Viewer.file_info` reports. */
export interface FileFacts {
  objects: number;
  models: number;
  triangles: number;
  support_triangles: number;
  /** Width, depth, height (mm); empty without geometry. */
  size: ArrayLike<number>;
  /** Label, value, label, value… */
  facts: string[];
}

const count = (n: number) => n.toLocaleString("en-US");

/** The panel's rows, label and value. */
export function infoRows(name: string, i: FileFacts): [string, string][] {
  const rows: [string, string][] = [["Path", name]];
  const supports = i.objects - i.models;
  if (i.objects > 1) {
    rows.push(["Objects", `${i.models} model${i.models === 1 ? "" : "s"}${supports ? `, ${supports} support${supports === 1 ? "" : "s"}` : ""}`]);
  }
  rows.push(["Triangles", count(i.triangles)]);
  if (i.support_triangles > 0) rows.push(["Support triangles", count(i.support_triangles)]);
  if (i.size.length === 3) {
    const mm = (v: number) => `${v.toFixed(2)} mm`;
    rows.push(["Width (X)", mm(i.size[0])], ["Depth (Y)", mm(i.size[1])], ["Height (Z)", mm(i.size[2])]);
  }
  for (let k = 0; k + 1 < i.facts.length; k += 2) rows.push([i.facts[k], i.facts[k + 1]]);
  return rows;
}

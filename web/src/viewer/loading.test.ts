import { evictHidden, planBatches, splitBundle } from "./loading";

/** A bundle as `stl_core::view::encode_bundle` writes it. */
function bundle(files: [string, boolean, Uint8Array][]): ArrayBuffer {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [enc.encode("MPKB")];
  const u32 = (n: number) => {
    const b = new Uint8Array(4);
    new DataView(b.buffer).setUint32(0, n, true);
    return b;
  };
  parts.push(u32(files.length));
  for (const [name, ok, bytes] of files) {
    const n = enc.encode(name);
    parts.push(u32(n.length), n, new Uint8Array([ok ? 1 : 0]), u32(bytes.length), bytes);
  }
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out.buffer;
}

describe("viewer loading", () => {
  it("groups files into batches by count and size", () => {
    expect(planBatches(["a", "b", "c"], {}, 2, 100)).toEqual([["a", "b"], ["c"]]);
    expect(planBatches(["a", "b", "c"], { a: 60, b: 60, c: 10 }, 8, 100)).toEqual([["a"], ["b", "c"]]);
    expect(planBatches(["big", "a"], { big: 500, a: 1 }, 8, 100)).toEqual([["big"], ["a"]]);
    expect(planBatches([], {})).toEqual([]);
  });

  it("splits the server's bundle into scenes and errors", () => {
    const data = bundle([
      ["parts/a ✓.stl", true, new Uint8Array([1, 2, 3])],
      ["b.obj", false, new TextEncoder().encode("bad file")],
    ]);
    const files = splitBundle(data);
    expect(files[0]).toEqual({ name: "parts/a ✓.stl", scene: new Uint8Array([1, 2, 3]) });
    expect(files[1]).toEqual({ name: "b.obj", error: "bad file" });
    expect(() => splitBundle(data.slice(0, data.byteLength - 1))).toThrow("cut short");
    expect(() => splitBundle(new TextEncoder().encode("MPKS\0\0\0\0").buffer)).toThrow("not a bundle");
  });

  it("forgets the oldest hidden files beyond the limits", () => {
    const hidden = [
      { name: "a", triangles: 3 },
      { name: "b", triangles: 3 },
      { name: "c", triangles: 3 },
    ];
    expect(evictHidden(hidden, 2, 100)).toEqual(["a"]);
    expect(evictHidden(hidden, 8, 5)).toEqual(["a", "b"]);
    expect(evictHidden(hidden, 8, 100)).toEqual([]);
  });
});

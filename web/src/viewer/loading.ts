// How the viewer loads files, as pure functions: requests grouped into
// batches (`POST /api/models/{id}/view`), the bundle the server answers with
// split into scenes, and which hidden files to forget.

/** A batch of files for one request: few enough, and small enough, to hold in memory at once. */
export const BATCH_FILES = 8;
export const BATCH_BYTES = 256 << 20;

/**
 * `names` in order, grouped into batches of at most `maxFiles` files and
 * `maxBytes` bytes (by their sizes in the archive; unknown sizes count as 0).
 * A file larger than `maxBytes` gets a batch of its own.
 */
export function planBatches(
  names: string[],
  sizes: Record<string, number | null | undefined>,
  maxFiles = BATCH_FILES,
  maxBytes = BATCH_BYTES,
): string[][] {
  const batches: string[][] = [];
  let batch: string[] = [];
  let bytes = 0;
  for (const name of names) {
    const size = sizes[name] ?? 0;
    if (batch.length && (batch.length >= maxFiles || bytes + size > maxBytes)) {
      batches.push(batch);
      batch = [];
      bytes = 0;
    }
    batch.push(name);
    bytes += size;
  }
  if (batch.length) batches.push(batch);
  return batches;
}

/** One file of a bundle: its scene (MeshPeek's wire format) or why it has none. */
export type BundleFile = { name: string; scene: Uint8Array } | { name: string; error: string };

/**
 * The files of the server's bundle (`stl_core::view::encode_bundle`):
 * `"MPKB" u32:files { u32:len, name, u8:ok, u32:len, bytes }`, little-endian.
 * Scenes are views into `data`, not copies.
 */
export function splitBundle(data: ArrayBuffer): BundleFile[] {
  const view = new DataView(data);
  const text = new TextDecoder();
  let at = 0;
  const take = (n: number) => {
    if (at + n > data.byteLength) throw new Error("the server's answer is cut short");
    const bytes = new Uint8Array(data, at, n);
    at += n;
    return bytes;
  };
  const u32 = () => {
    take(4);
    return view.getUint32(at - 4, true);
  };
  if (text.decode(take(4)) !== "MPKB") throw new Error("the server's answer is not a bundle of scenes");
  const files: BundleFile[] = [];
  for (let n = u32(); n > 0; n--) {
    const name = text.decode(take(u32()));
    const ok = take(1)[0] === 1;
    const bytes = take(u32());
    files.push(ok ? { name, scene: bytes } : { name, error: text.decode(bytes) });
  }
  return files;
}

/** Hidden files the viewer keeps (shown again at once), at most this many and this many triangles. */
export const KEEP_HIDDEN_FILES = 8;
export const KEEP_HIDDEN_TRIANGLES = 4_000_000;

/** The hidden files (oldest first) to forget so that the rest fit the limits. */
export function evictHidden(
  hidden: { name: string; triangles: number }[],
  maxFiles = KEEP_HIDDEN_FILES,
  maxTriangles = KEEP_HIDDEN_TRIANGLES,
): string[] {
  let triangles = hidden.reduce((n, h) => n + h.triangles, 0);
  let count = hidden.length;
  const drop: string[] = [];
  for (const h of hidden) {
    if (count <= maxFiles && triangles <= maxTriangles) break;
    drop.push(h.name);
    count -= 1;
    triangles -= h.triangles;
  }
  return drop;
}

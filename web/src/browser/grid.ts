// Thumbnail grid layout: columns for a width, card sizes, which id pages a
// range of visible rows needs, and the lines a card shows below its image.
import type { Schemas } from "../api/client";
import { baseName, formatDate, formatSize } from "../format";

export const CARD_WIDTH = 180;
export const CARD_GAP = 10;
export const GRID_PAGE = 120;
/** The card widths the size slider offers (thumbnails are made up to 512 px). */
export const CARD_MIN = 120;
export const CARD_MAX = 480;
const CARD_KEY = "stl.folderCardWidth";
/** The image grid's card width (the Folders page keeps its own). */
export const GRID_CARD_KEY = "stl.gridCardWidth";
const INFO_KEY = "stl.gridInfo";

/** A card's image height and row height for a card `width` with `lines` lines
 *  below the title (180 with one → 150 and 220). */
export function cardLayout(width: number, lines = 1): { image: number; row: number } {
  const image = Math.round((width * 5) / 6);
  return { image, row: image + 52 + 18 * lines };
}

/** What a card can show below its title, in display order. */
export const CARD_INFO = [
  { value: "creator", label: "Creator" },
  { value: "name", label: "File name" },
  { value: "path", label: "Path" },
  { value: "size", label: "File size" },
  { value: "date", label: "Date" },
  { value: "added", label: "Added" },
] as const;
export type CardInfo = (typeof CARD_INFO)[number]["value"];

/** The lines a card shows for `shown`, in [`CARD_INFO`] order (a missing value is blank). */
export function infoLines(cell: Schemas["Cell"], shown: readonly CardInfo[]): string[] {
  const value: Record<CardInfo, () => string> = {
    creator: () => cell.creator ?? "",
    name: () => baseName(cell.archive_path),
    path: () => cell.archive_path ?? "",
    size: () => (cell.archive_size != null ? formatSize(cell.archive_size) : ""),
    date: () => formatDate(cell.release_year, cell.release_month),
    added: () => cell.created_at?.slice(0, 16) ?? "",
  };
  return CARD_INFO.filter((i) => shown.includes(i.value)).map((i) => value[i.value]());
}

/** The image grid's sort choices: the table's sort keys. */
export const GRID_SORTS = [
  { value: "title", label: "Title" },
  { value: "creator", label: "Creator" },
  { value: "release_date", label: "Date" },
  { value: "archive_size", label: "File size" },
  { value: "created_at", label: "Added" },
  { value: "archive_path", label: "Path" },
  { value: "sculptor_name", label: "Sculptor" },
  { value: "collection_name", label: "Collection" },
];

/** The card lines last chosen in this browser (creator by default). */
export function loadCardInfo(): CardInfo[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(INFO_KEY) ?? "null");
    if (Array.isArray(v)) return CARD_INFO.map((i) => i.value).filter((k) => v.includes(k));
  } catch {
    /* fall through */
  }
  return ["creator"];
}

export function saveCardInfo(shown: readonly CardInfo[]): void {
  try {
    localStorage.setItem(INFO_KEY, JSON.stringify(shown));
  } catch {
    /* a convenience only */
  }
}

/** `width` within the slider's range. */
export function clampCard(width: number): number {
  return Math.round(Math.min(Math.max(width, CARD_MIN), CARD_MAX));
}

/** The card width last chosen in this browser (it depends on the screen, not the account). */
export function loadCardWidth(key = CARD_KEY): number {
  try {
    const v = Number(localStorage.getItem(key));
    return v ? clampCard(v) : CARD_WIDTH;
  } catch {
    return CARD_WIDTH;
  }
}

export function saveCardWidth(width: number, key = CARD_KEY): void {
  try {
    localStorage.setItem(key, String(clampCard(width)));
  } catch {
    /* a convenience only */
  }
}

/** Columns that fit `width` (at least one). */
export function columnsFor(width: number, card = CARD_WIDTH, gap = CARD_GAP): number {
  return Math.max(1, Math.floor((width + gap) / (card + gap)));
}

/** The page numbers (of `pageSize` items) covering rows `first`..`last` of `cols` columns. */
export function pagesFor(first: number, last: number, cols: number, total: number, pageSize = GRID_PAGE): number[] {
  if (total <= 0 || last < first) return [];
  const from = Math.max(0, first * cols);
  const to = Math.min(total - 1, (last + 1) * cols - 1);
  if (to < from) return [];
  const out: number[] = [];
  for (let p = Math.floor(from / pageSize); p <= Math.floor(to / pageSize); p++) out.push(p);
  return out;
}

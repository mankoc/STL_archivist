// Display formatting shared by the views.

/** Bytes as B / KB / MB / GB (the desktop's File Size column). */
export function formatSize(bytes: number | null | undefined): string {
  if (bytes == null) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${value} B` : `${value.toFixed(1)} ${units[unit]}`;
}

/** `YYYY-MM`, `YYYY` or empty. */
export function formatDate(year: number | null | undefined, month: number | null | undefined): string {
  if (year == null) return "";
  return month == null ? String(year) : `${year}-${String(month).padStart(2, "0")}`;
}

/** The last path component (either separator). */
export function baseName(path: string | null | undefined): string {
  if (!path) return "";
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] ?? "";
}

// The accent colour: buttons, selections and highlights (Mantine's primary
// colour), one of Mantine's palette, chosen per user in Settings → Account
// (`/api/me/settings` key `primary_color`). This browser remembers the last
// one so the page starts in it before the settings arrive.
import { createContext, useContext } from "react";

export const ACCENTS = ["blue", "indigo", "violet", "grape", "pink", "red", "orange", "yellow", "lime", "green", "teal", "cyan", "gray"] as const;
export type Accent = (typeof ACCENTS)[number];
export const DEFAULT_ACCENT: Accent = "blue";
/** The per-user setting's key. */
export const ACCENT_SETTING = "primary_color";

export const isAccent = (v: unknown): v is Accent => typeof v === "string" && (ACCENTS as readonly string[]).includes(v);

const KEY = "stl.accent";

/** The accent this browser used last (the default when none, or unreadable). */
export function savedAccent(): Accent {
  try {
    const v = localStorage.getItem(KEY);
    return isAccent(v) ? v : DEFAULT_ACCENT;
  } catch {
    return DEFAULT_ACCENT;
  }
}

export function rememberAccent(accent: Accent): void {
  try {
    localStorage.setItem(KEY, accent);
  } catch {
    /* a convenience only */
  }
}

/** Changes the accent of the whole page (provided in main.tsx). */
export const AccentContext = createContext<(accent: Accent) => void>(() => {});
export const useSetAccent = () => useContext(AccentContext);

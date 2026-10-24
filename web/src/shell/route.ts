// Pages addressed by the URL hash (`#/jobs`), so they survive a reload and
// can be bookmarked; no router library needed for a handful of pages.
import { useEffect, useState } from "react";

export const PAGES = ["library", "folders", "find", "duplicates", "lookups", "libraries", "jobs", "stats", "settings", "help"] as const;
export type Page = (typeof PAGES)[number];

/** Pages that became something else: where their links lead now. */
const MOVED: Record<string, string> = { maintenance: "settings/maintenance" };

/** `hash` with a moved page's address replaced by its new one (else unchanged). */
export function currentHash(hash: string): string {
  const path = hash.replace(/^#\/?/, "");
  const [name, ...rest] = path.split("/");
  const to = MOVED[name];
  return to ? `#/${[to, ...rest].join("/")}` : hash;
}

export function pageFromHash(hash: string): Page {
  const name = currentHash(hash).replace(/^#\/?/, "").split(/[/?]/)[0];
  return (PAGES as readonly string[]).includes(name) ? (name as Page) : "library";
}

/** Rewrite a moved page's address in place (no new history entry). */
function redirect() {
  const to = currentHash(location.hash);
  if (to !== location.hash) history.replaceState(null, "", to);
}

export function usePage(): [Page, (p: Page) => void] {
  const [page, setPage] = useState<Page>(() => {
    redirect();
    return pageFromHash(location.hash);
  });
  useEffect(() => {
    const onHash = () => {
      redirect();
      setPage(pageFromHash(location.hash));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return [page, (p) => (location.hash = p === "library" ? "" : `#/${p}`)];
}

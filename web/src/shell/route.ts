// Pages addressed by the URL hash (`#/jobs`), so they survive a reload and
// can be bookmarked; no router library needed for a handful of pages.
import { useEffect, useState } from "react";

export const PAGES = ["library", "find", "duplicates", "lookups", "jobs", "maintenance", "stats", "settings"] as const;
export type Page = (typeof PAGES)[number];

export function pageFromHash(hash: string): Page {
  const name = hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return (PAGES as readonly string[]).includes(name) ? (name as Page) : "library";
}

export function usePage(): [Page, (p: Page) => void] {
  const [page, setPage] = useState<Page>(() => pageFromHash(location.hash));
  useEffect(() => {
    const onHash = () => setPage(pageFromHash(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return [page, (p) => (location.hash = p === "library" ? "" : `#/${p}`)];
}

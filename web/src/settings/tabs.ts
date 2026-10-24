// Settings tabs in the URL (`#/settings/components`), so other pages can link to one.

/** The tab named after `#/settings/`, when it is one of `tabs`; else the first. */
export function settingsTab(hash: string, tabs: string[]): string {
  const name = hash.replace(/^#\/?/, "").split(/[/?]/)[1] ?? "";
  return tabs.includes(name) ? name : tabs[0];
}

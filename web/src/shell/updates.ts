// The update check (Settings → Account): off unless the user turns it on.

/** The per-user setting (`/api/me/settings`): the server asks GitHub for new releases. */
export const CHECK_UPDATES = "check_updates";

/** The server keeps GitHub's answer for a day; asking hourly finds a new one soon after. */
export const UPDATE_REFETCH_MS = 60 * 60 * 1000;

const DISMISSED_KEY = "stl.update.dismissed";

/** The version to announce: a newer one the user has not dismissed. */
export function versionToShow(latest: string | null | undefined, dismissed: string | null): string | null {
  return latest && latest !== dismissed ? latest : null;
}

export function loadDismissed(): string | null {
  try {
    return localStorage.getItem(DISMISSED_KEY);
  } catch {
    return null;
  }
}

export function saveDismissed(version: string): void {
  try {
    localStorage.setItem(DISMISSED_KEY, version);
  } catch {
    // Only a convenience: the banner comes back on the next visit.
  }
}

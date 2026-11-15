# Pending work

What's left from the full code review of 2026-11-06. Finished items are removed, so the numbers have gaps.

**Most of this comes from reading the code, not from a failing test or a measurement**, so the real impact isn't
confirmed. Reproduce each problem before changing it, and turn the reproduction into a regression test.

## Before the next release

- [ ] Work through `FEATURE_REVIEW_CHECKLIST.md`, starting with the items marked (UI). Edge with Python Playwright
      can drive the real UI on this machine.
- [ ] Decide whether to commit the checklist and `RELEASE_NOTES_NEXT.md`.
- [ ] Quit the desktop app after an edit and check the sidecar was written, and try a real update. The server side of
      quitting is checked (a real server, an edit, stdin closed: the sidecar is written); the app itself isn't.
- [ ] Release 2026.45.41 or later: 2026.45.40 went out without the fixes for warnings in the detail pane and for filtering
      by Path with other filters. `RELEASE_NOTES_NEXT.md` still holds the 2026.45.40 notes: start it again from the changelog.

## Speed and memory

Measured as fine, so not worth changing: `prepare_cached` (saves 2 µs a statement), session checks (0.3 ms), row and grid queries (2–3 ms), SQLite memory at
100,000 models (about 130 MB peak).

## Code structure


## Project setup

- [ ] Add ESLint (needs npm to update `package-lock.json`). The audit job in CI reports only, and could fail runs once
      it's clean.

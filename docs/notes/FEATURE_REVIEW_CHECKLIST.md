# Feature review checklist

For versions 2026.45.22 to 2026.45.28, before tagging a release. Use a copy of a real library. Automated
tests cover the server side of everything here, but **none of the new screens has been opened in a browser
yet**, so the items marked (UI) have never been seen running.

## New features

### Merge similar (Lookups page)

- [x] (UI) *Merge similar…* appears on every Lookups tab and on Creators, for editors only.
- [x] (UI) The dialog opens and lists pairs, with model counts, and the more-used name first.
- [x] *Similar spelling*: changing *Letters that differ* changes the list; `Alice` and `ALICE` appear at 0.
- [x] *Sounds alike* and *Shared words* give sensible results on your real tags. *Shared words* is the likeliest
      to be noisy: `dragon` pairs with `red dragon`.
- [x] (UI) Clicking a name merges the pair; the list refreshes and the models show the kept name.
- [ ] (UI) *Merge the two selected* works on the Tags and Scales tabs (it failed before).
- [ ] Subcollections with the same name in different collections are not offered as a pair.
- [ ] On your largest tag list the scan feels quick. It hasn't been timed on a big library.
- [ ] Decide: sculptors and releases have no Lookups tab, so they can't be merged. Is that enough?

### Files window highlights warnings

- [ ] (UI) On a model with *Nested archives* or *Models with presupported stl*, the responsible files are orange
      with a warning icon, and hovering names the warning.
- [ ] (UI) Folders holding those files are orange, including when closed.
- [ ] (UI) The header reads "N files, M with a warning", and the CRC column is gone.
- [ ] (UI) A warning raised by something absent (*Archive without images*) highlights nothing.
- [ ] (UI) A model with no warnings looks as before, and its archive isn't opened for this.
- [ ] (UI) An archive that's missing from disk still shows its stored file list.

### Per-folder path patterns

- [ ] (UI) *Crawl with a pattern…* appears in the Path section's right-click menu, pre-filled with the folder's
      current pattern.
- [ ] Start it on a subfolder: the fields fill from the new pattern, read from the library's root.
- [ ] (UI) The Libraries page lists it under the library folder as `folder: pattern`.
- [ ] A later full crawl, a monitored crawl and *Update all* all use it for new archives in that folder.
- [ ] Rescan fills an emptied field from the pattern, and leaves filled fields alone.
- [ ] An empty pattern in the dialog goes back to the library's, and the Libraries line disappears.
- [ ] Decide: stored patterns can only be changed through that dialog, and aren't saved in `METADATA.7z`
      sidecars. Is that enough?

### Earlier this release

- [ ] Table sorts by Date, with undated models last in both directions.
- [ ] Sort files: *Missing fields* (unknown, nothing, your text), and the last target folder is remembered.
- [ ] Jobs page: *Clear finished* keeps jobs holding results.
- [ ] Model viewer: an `.ags` file shows its raft and bracing.

## Safety fixes, on real files

- [ ] Sort two folders that each hold a `cover.jpg` into one folder: the preview shows it, and both files survive.
- [ ] Sort archives already in place with the pattern that put them there: nothing is renamed.
- [ ] Move an archive out of a monitored library, wait for a check, move it back: the missing mark clears, and
      the record keeps its edits.
- [ ] **Unmount a share or disconnect a drive holding a library, leaving the mount point empty**, then crawl: the
      records are marked missing, nothing is deleted, and the job says so. Reconnect and crawl: all normal.
- [ ] The same library in *Update all*, and as a monitored folder: nothing is deleted.
- [ ] Docker without UnRAR: a compressed `.rar` keeps its file list, with no false warnings and *images not read*
      in the log.
- [ ] Delete one archive on purpose and crawl: its record is still deleted, and the job's message counts it.

## Behaviour changes to confirm you're happy with

- [ ] Hiding a value keeps models with no value. Check it on Warnings, Creators and Date.
- [ ] Exact creator matching: `Alice` and `ALICE` are separate rows and no longer select each other.
- [ ] A folder emptied on purpose is marked missing, not deleted. Does *Remove missing archives* feel like the
      right way out?
- [ ] A library rooted at a drive with a permanently unreadable folder (`System Volume Information`): *Update
      all* never cleans up its marked records, and the crawl warns every time.
- [ ] Custom presupport patterns: anything that compiles is now accepted, including heavy expressions.

## Performance, on the real library

- [ ] First crawl of a folder with many large images: memory stays low.
- [ ] A folder of many archives with shared cover images crawls quickly.
- [ ] Filter clicks feel immediate. Folder filters are the slowest case.
- [ ] Thumbnails are the same as before for ZIP. Spot-check a few new `.7z` crawls.

## Upgrade and compatibility

- [ ] An existing data directory opens and gains `folder_patterns` without trouble.
- [ ] Backup, then restore. A sync between two servers on this version, then one with an older receiver: it's refused.
- [ ] A sidecar written by this version imports correctly, in a new library and in the previous release.
- [ ] Desktop app: start, open a local library, close, reopen.
- [ ] Build the installer, and the Docker image. The Docker image and Linux aren't exercised by regular CI.

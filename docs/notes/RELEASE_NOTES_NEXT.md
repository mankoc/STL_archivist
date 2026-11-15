# StlArchivist — next release

Changes since v2026.45.16. **Please update:** this release fixes bugs that could overwrite files and lose
records. See the first section.

## Your files and records are safer

- **Sort files no longer overwrites files.** When two files were headed for the same name, such as a
  `cover.jpg` from each of two folders, the second replaced the first. Each now gets its own name, and the
  preview marks it *COLLISION*.
- **A crawl no longer deletes the record of an archive that is on disk.** If an archive had been marked
  missing and then came back, the next crawl deleted its record, along with your edits. The mark now clears
  by itself.
- **A disconnected drive or share can no longer wipe a library.** A library that comes back empty has its
  records marked missing rather than deleted, and they return to normal when the archives are found again.
  Records in a folder that can't be read are left alone. Crawl results now say how many records were moved,
  deleted or marked missing.
- **Security:** a `..` in a Sort files pattern or field value could move archives outside the target folder,
  and a Lychee `.lys` file could make the server open a path named inside it. Both are fixed.

- **A damaged archive can't crash the server any more.** A ZIP claiming an impossible size used to end the whole
  process.
- **Delete files and Sort files find archives with decomposed Unicode names** (some names from macOS). Delete files
  used to remove the record and leave the file.
- **Renaming, merging or deleting a tag, collection, creator and the like now updates `METADATA.7z`.** Sidecars used
  to keep the old name.
- **Closing the desktop app no longer loses sidecars of your latest edits.** It now stops its server cleanly.
- **Updates check the installer's checksum** and refuse a download that differs.
- **Sign-in is harder to abuse:** very long names are refused at once, and only four password checks run at a time.

## New

- **Merge similar** on the Lookups page finds near-duplicate tags, categories, collections, subcollections,
  scales and creators, by spelling, sound or shared words. Click the name to keep.
- **Folders keep their path pattern.** A folder inside a library can have its own (*Crawl with a pattern…* in
  the filter pane's folder menu). Later crawls, monitoring and Rescan all use it.
- **The Files window highlights the files behind a model's warnings**, and pointing at one says which warning
  it triggers. The CRC column is gone.
- **Sort by Date** in the table: by release year, then month.
- **Sort files** can choose what missing fields become (*unknown*, nothing, or your own text), and remembers
  the last target folder.
- **Clear finished** on the Jobs page.
- **Custom presupport patterns** can be any regular expression.
- **Model viewer:** MeshPeek 0.5.9, so `.ags` files show their raft and bracing.

- **The Lookups page has Sculptors and Releases tabs,** with rename, delete, merge and *Merge similar*.
- **A sub-folder's path pattern can be changed or forgotten on the Libraries page,** and its sidecar keeps it.

## Faster and lighter

| | Before | After |
|---|---|---|
| Peak memory, crawling 50 archives of 60 images | 3 GB | under 80 MB |
| Crawling 200 archives that share 5 images | 54 s | under half a second |
| Filtering 100,000 models by creator | 880 ms | 13 ms |
| Filtering by tag | 1,060 ms | 240 ms |
| Filtering by folder | 700 ms | 130 ms |

Measured on a synthetic test library; your numbers will vary.

- **Responses are compressed**, which helps on slower links, and downloads read 256 KB at a time.
- **A burst of library changes is one reload** in the web client.
- **Edits don't wait while a crawl reads a sidecar.**
- **Generate Thumbnails is about nine times faster,** and Find by Image reads and holds far less.
- **Job progress no longer sends every client the job's whole list of models.**
- **Pages load when opened,** so the web client starts with a smaller script.
- **RAR archives read far faster with UnRAR:** one run for all the members needed, not one each.
- **Crawls leave other drives asleep,** and editing a title no longer reloads the filter pane.

## Fixed

- **An encrypted image in a ZIP no longer hides the archive's other images.**
- **The desktop app keeps the previous server log** as `stl-server.previous.log`.
- An archive whose images can't be read (a `.rar` without UnRAR, encrypted members) keeps its file list and no
  longer gets false *without images* and *without printable files* warnings.
- Hiding a filter value keeps models with no value there. Hiding one warning used to hide every model with no
  warnings at all.
- Creators and types starting with a capital like *É* can be selected in the filter pane.
- *Merge the two selected* works for tags and scales.
- Sidecars that can't be imported or written are now reported in the crawl's log.
- An archive already where Sort files would put it stays put, instead of being renamed `name_1`.

## Worth knowing when you update

- **A crawl now judges only the folder it crawls:** an archive gone from elsewhere is marked missing when its own folder
  is crawled. Moves between libraries are still found.
- **The database gains one small table**, added by itself at start. No action is needed.
- **Creator and type filters are now exact:** `Alice` no longer also selects `ALICE`. *Merge similar* joins them.
- **A folder you emptied on purpose** has its records marked missing rather than deleted. *Settings →
  Maintenance → Remove missing archives* clears them.
- **New crawls of `.7z` archives** may pick different preview images than before. Existing thumbnails don't change.
- **Compressed `.rar` archives crawled earlier without UnRAR** kept no file list and may show false warnings. A
  Rescan fixes them.

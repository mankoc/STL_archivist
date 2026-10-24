# StlArchivist user guide

StlArchivist keeps a catalogue of your 3D-printable model archives (`.zip`, `.7z`, `.rar`):
titles, creators, sculptors, collections, tags, scales, preview images. It reads the archives
where they are; nothing is unpacked or copied.

You use it in a web browser, or in the **StlArchivist** desktop app, which shows the same
pages in its own window. Installing and running the server is covered in
[deployment.md](deployment.md). The same guide, in more depth and searchable, is built into the
interface: the **?** button at the top right (or *Help* in the menu under your name).

- [Getting started](#getting-started)
- [The pages](#the-pages)
- [Adding archives: crawling](#adding-archives-crawling)
- [Browsing and finding models](#browsing-and-finding-models)
- [Editing](#editing)
- [Working with archives](#working-with-archives)
- [Duplicates and Find by Image](#duplicates-and-find-by-image)
- [AI tagger](#ai-tagger)
- [Maintenance](#maintenance)
- [Settings and accounts](#settings-and-accounts)
- [Path patterns](#path-patterns)
- [Keyboard and mouse](#keyboard-and-mouse)

## Getting started

**Desktop app, library on this computer.** Start *StlArchivist*. Under **This computer**,
choose the library folder (a new, empty folder starts a new library; an existing one, such as
a library made by the earlier version (then called stlLibrary), opens as it is) and click **Open**.

**Desktop app or browser, shared server.** In the desktop app, add the server's address
(for example `http://nas:8770`) under **Servers** and click **Connect**. In a browser, open
that address.

**First time.** A new library asks for an administrator account: a name and a password of
at least 8 characters. Afterwards everyone signs in with their own account (see
[Settings and accounts](#settings-and-accounts)).

What you can do depends on your role: **viewers** browse; **editors** also edit, crawl and run
maintenance; **administrators** also manage users, library settings and backups. Buttons you
cannot use are hidden or disabled.

## The pages

The bar at the top switches pages:

| Page | For |
|---|---|
| **Library** | Browsing, searching, filtering and editing models |
| **Folders** | Browsing archives folder by folder, as thumbnails |
| **Find by Image** | Finding models that look like a picture |
| **Duplicates** | Archives that share files or images; models with similar names |
| **Lookups** | Tags, categories, collections, subcollections, scales, creators |
| **Libraries** | Libraries and their folders: add, crawl, monitor, rename, move, remove |
| **Jobs** | Every background job with its log |
| **Maintenance** | Checking and repairing the library, sidecars, backups |
| **Statistics** | Totals, rankings, and the distribution of any column |
| **Settings** | Your account, the library's settings, components, users |

The **status bar** at the bottom of every page shows the running job with its progress (and
how many wait behind it; click it for the Jobs page), and how many archives all libraries
hold.

The **?** button opens the built-in help: every topic of this guide in more depth, with a
search box; the **?** beside some cards' titles opens the topic about that card. The menu at
the top right (your name) has **Help**, **About**, **Change password** and **Sign out**.

## Adding archives: crawling

A *crawl* reads a folder of archives (and its subfolders) and catalogues every archive:
it reads the archive's file list, preview images, `info.json` and `.url` files, works out
scales and presupports, and takes metadata from the folder names when you give a
[path pattern](#path-patterns).

1. Open **Libraries** and click **Add library**.
2. Browse to the folder that holds your archives and click **Use this folder**.
3. In *Crawl folder*, optionally enter a path pattern such as `{sculptor}/{release}/{title}`.
   Tick **Force rescan** only to re-read archives that have not changed.
4. Click **Crawl**.

The crawl runs on the server as a **job**: its progress and log appear on the right of the
Jobs page, and you can leave the page, close the browser or use the app meanwhile. Jobs run
one at a time; new ones wait their turn. The stop button stops a job at its next safe point,
keeping the work already done.

Later crawls are quick: an archive whose fingerprint has not changed is skipped. Moved
archives are recognised by their content and keep their record, edits and images.

- **Update all** re-crawls every folder in the list with its saved pattern, matches archives
  moved from one folder to another, and removes the records of archives that are gone.
- Each folder in the list has buttons to crawl it alone or to remove it from the list
  (removing it from the list does not delete records or files).

Editors can only choose folders the administrator made available; administrators can choose
any folder on the server.

**Libraries.** A *library* is a name and one or more folders — your miniatures on two disks
can be one library. **Add library** makes a new one from a folder; **Add folder** (the folder
button on a library) adds another folder to it. Each folder has its root and its own path
pattern, and is crawled, monitored and moved on its own. Records store their archive's path
relative to their folder, the same on every operating system. When a folder moves (another
disk, a new mount point, a new server, from Windows to Linux), click **Move** on it, choose
the new place, **Check** (every archive is looked up there) and **Move**: records, edits,
tags and thumbnails stay and nothing is re-read. A folder that is gone shows *root not
found*. **Move to another library** (on a folder) takes the folder and its records to another library or
a new one, nothing re-read. **Rename** changes the library's name. Removing a folder gives its records to the
folder around it, or deletes them when there is none (the files stay); a library left
without folders goes too, and **Remove library** removes all its folders. A folder may sit
inside another: an archive belongs to the deepest folder above it, and the outer folder's
crawls skip the inner one. The browser has a **Library** filter section and column, and its
**Path** section lists each library, then its folders, then their sub-folders.

**Monitored folders.** The eye button on a library monitors it: every *Check monitored
folders every* minutes (*Settings → Library*, default 30) the server compares the folder with
the library by file size and date, without opening archives, and crawls it only when archives
were added, changed or removed (the job shows as started by `monitor`). Archives that vanished
are marked missing instead of deleted; *Maintenance → Remove missing archives* deletes their
records.

> In the desktop app's *This computer* mode, the server belongs to the window: closing the
> window stops a running crawl. The next crawl or *Update all* continues from there.

## Browsing and finding models

The **Library** page has the filter pane on the left, the models in the middle and the
selected model's details on the right.

### Search and filters

- **Search** (above the table) matches titles, creators, paths and tags.
- **Filter pane** — one section per field (Creators, Tags, Type, Date, Sculptor, Collection,
  Category, Release, Scale, Presupports, Warnings, File Type, Path). Click the arrow to open a
  section, and click a value to cycle it: **+** (green) shows only models with that value,
  **−** (red) hides them, a third click clears it. Several values combine.
- The **circle on a section's header** cycles *has a value* / *has no value* / off, for
  example "models without tags".
- The numbers beside values count the models each would match together with the other
  active filters. Long sections have their own search box.
- **Clear** (top of the pane) removes every filter.

### Table and grid

- Click a column header to sort (ascending, descending, off); drag a header's edge to resize.
- **Columns** chooses which columns are shown (saved with your account). The standard
  columns are on until you hide them; *More columns* lists further fields, off until you turn
  them on: Warning, Sequence, Images, Compression, To print, Missing, Added, Updated, URL,
  Description, Folder and Archive hash.
- **Table / Images** switches between the table and a grid of thumbnails of the same models.
- Click a row to select it; **Ctrl**-click adds or removes rows; **Shift**-click selects a range.
  The count of selected models shows above the table.

### Details pane

The selected model's fields, tags, warnings, archive path and images. Drag the pane's left
edge to make it wider or narrower (double-click the edge to reset); the width is remembered
in this browser.

- **Download** the archive; **Copy path** (when the server's path makes sense on your
  computer); **Open folder** in the desktop app shows the archive in the file manager.
- **Files** lists every file inside the archive with its size and CRC-32, as the last crawl
  or rescan read the archive's catalog.
- Warnings (for example *Archive without images*) show as orange badges; **Warning
  details** lists the files that trigger each one.
- Click an image to view it full screen at full resolution: the original is read from the
  archive (the thumbnail shows meanwhile, and stays when the original cannot be read, for
  example for an image added by hand). **←** / **→** or the side arrows step through the
  model's images; the link at the top right opens the original in a new tab.

### Folders page

Lists the folders that hold catalogued archives (filter the list with the box above it).
Pick a folder to see its archives as thumbnails; **Include subfolders** adds the archives
below it. Click a thumbnail for the model's details.

### Statistics

Totals (models, images, sizes, presupported, with warnings, …), the most frequent values per
field, and **Column distribution**: pick a field to see how the models spread over its
values.

## Editing

Editing needs the editor role.

- **In the table** — double-click a cell, type, press **Enter** to save or **Esc** to cancel.
  Tags and scales are comma-separated.
- **Model editor** — **Edit** in the details pane: every field with suggestions from the
  library (title, creator, type, date `YYYY-MM` or `YYYY`, sequence, sculptor, release,
  collection, subcollection, category, presupports, scales, tags, URL, description).
- **Images** — drop image files on the details pane (or click the drop area) to add them; the
  × on an image removes it.
- **Mass edit** — select several models, then **Mass edit**:
  - *Path pattern* takes values from each model's folder path; **Preview** shows what it
    finds for every model before saving.
  - *Fields* sets values for all of them; **Clear** empties a field. With the *replace*
    switch on, typed and parsed values replace existing ones; otherwise they only fill empty
    fields. Scales and tags are added.
  - *Find & replace* changes text in the chosen fields (optionally case-sensitive or a
    regular expression); *Trim* removes stray spaces.
- **Remove from library** removes the selected records (and their images) from the catalogue;
  the archive files stay. Crawling the folder again brings them back.
- **Lookups** page — tags, categories, collections, subcollections and scales: rename,
  delete (it shows how many models lose the value first), merge two entries (for tags: merge
  the selected tags into one) and *delete unused tags*. The **Creators** tab renames,
  merges and clears creators, and edits a creator's profile.

Edits update the folder's `METADATA.7z` sidecar shortly afterwards when sidecars are on.

## Working with archives

Select models, then open **Archives** above the table:

- **Rescan** re-reads the archives (metadata, file list, presupports, warnings, scales). Use it
  after replacing an archive's contents in place.
- **Generate thumbnails** replaces the models' images with new ones from their archives, up
  to the library's *thumbnails per archive*.
- **Sort files…** moves the archives into a folder tree built from a pattern, for example
  `{collection}/{creator}/{title}` under a target folder. **Preview** shows every move first
  (*COLLISION* means a file of that name exists, and a numbered name is used; *MISS* means the
  archive is not on disk). **Move** moves all of them or none: if one move fails, the others
  are put back. Needs *file sorting* on in the library settings.
- **Delete files…** deletes the archive files and removes their records. With a trash folder
  set in the library settings, the files are moved there with a small note (`.json`) for
  recovery. Needs *allow file delete* on in the library settings.

These run as jobs: follow them on the Jobs page.

## Duplicates and Find by Image

**Find by Image** — drop a picture (or click to choose one). *Perceptual hash* finds
near-identical images; move the slider to allow more differing bits. *AI embedding* (when the
server has it) finds the same model in other renders, angles and colours; the slider sets the
minimum similarity. Results update as you change the slider; click one for its details.

**Duplicates** has three tabs; each search runs as a job, and the newest result is shown again
when you come back:

- **STL files** — archive pairs that share at least the chosen share of their files (same name
  and size).
- **Images** — archive pairs with matching images, by perceptual hash (differing bits) or AI
  embedding (similarity). *Skip images in more than N models* ignores banners and logos that
  appear everywhere.
- **Metadata** — models with equal (*Exact*), almost equal (*Levenshtein*, up to N edits) or
  similar-sounding (*Soundex*) values in the chosen columns.

**Compare** opens the models side by side. Filter the results with the box above them.

## AI tagger

Select models, then **AI tagger** (when the server has the AI model installed; otherwise the
drawer says what is missing). The model looks at the thumbnails, the archive path, file names
and the metadata already catalogued.

- **Review one by one** — **Suggest** asks the model about the current model (the first
  request also loads the model, which can take a while). Edit the suggested tags (× removes,
  type and Enter adds) and fields, then **Save & next**, or **Skip**.
- **Process all** — tags every selected model in one job and saves the results.
- Options: **Max tags**; **Ignore catalogued metadata** (use only the images and names);
  **Overwrite filled fields** (by default only empty fields are filled; tags are always added
  to the existing ones).

## AI image embeddings and compute workers

*AI embedding* in Find by Image and Duplicates needs the CLIP encoder and ONNX Runtime
(*Settings → Components*) and *Compute missing embeddings*. ONNX Runtime has
a GPU variant — DirectML on Windows, CUDA on Linux — that the card preselects when it finds a
GPU; the card also shows where the encoder runs.

A server without a GPU can let a computer with one do the AI work: on that computer, open the
server in the desktop app, go to *Maintenance → Compute workers*, download the components
under **This computer** (**Change folder…** chooses where they go) and turn on **Use this
computer for AI**. The card lists the connected workers. A server set to `"compute":
"workers"` never runs the models itself: its AI jobs wait for a worker.

## Maintenance

The **Maintenance** page (editors; some cards for administrators):

- **Records**
  - **Verify archives** checks that every archive is still on disk, unchanged in size, and
    lists the problems (*Missing*, *Flagged Missing*, *Modified*).
  - **Find duplicate entries** lists records that catalogue the same archive twice.
  - On the list: **Find moved in DB** looks for each missing record's archive among the other
    records (the same content crawled elsewhere); **Merge moved** moves the missing record's
    metadata onto it. **Search in folder…** looks for the missing archives inside a folder you
    pick and points the records at them. **Remove selected** / **Remove all missing** delete
    records. Actions apply to the ticked rows, or to all matching rows when none is ticked.
  - **Remove missing archives** deletes the records of every archive marked missing (for
    example by monitored folders), after a confirmation. Archive files are not touched.
- **Warnings** — **Check images warning** and **Check presupport warning** re-read every
  archive's file list and fix just that warning, much faster than a rescan.
- **Images and storage** — compute missing (or all) image hashes and AI embeddings; **Prune
  images** keeps at most N images per model; **Compact database** (administrators) reclaims
  space after large deletions.
- **Metadata sidecars** — **Generate sidecars** writes `METADATA.7z` in every archive folder;
  **Clean sidecars** tidies the `METADATA.7z` files under the libraries: one that lists
  other archives than the folder's records is rewritten, one in a folder without records
  whose archives are all gone is deleted (one whose archives still exist is kept: crawling
  the folder imports them), and leftover `METADATA.7z.tmp` files are deleted. **Remove all
  sidecars** deletes them. Sidecars let a new library restore its records when it crawls the
  folders.
- **Components** (*Settings → Components*; administrators for the server's) — downloads the
  optional parts into the library's folder (in the desktop app also those of this computer's
  compute worker, when it uses another folder): the AI tagger's runtime (llama.cpp, with a *CPU* / *NVIDIA GPU (CUDA)*
  choice on Windows; CUDA is preselected when an NVIDIA GPU is found) and model (about 3.3 GB;
  tick the box accepting its licence, which limits commercial use), the CLIP encoder and
  ONNX Runtime (AI image embeddings), pdfium (PDF previews) and UnRAR (compressed RAR
  archives). Tick components, then
  **Download**; the card shows each one's size, licence and state (*installed*, *not
  installed*, or *configured elsewhere* when the server's configuration points at another
  copy). An interrupted download resumes where it stopped. **Remove selected** deletes
  downloaded ones. After installing the CLIP encoder and ONNX Runtime, run *Compute missing
  embeddings*.
- **Backups** (administrators) — **Back up now** saves the database, thumbnails and library
  settings in one file; download or delete backups from the list. Restoring is done on the
  server (see [deployment.md](deployment.md#backups-restore-and-upgrades)).
- **Delete all records** (administrators) — empties the library after you type `DELETE`.
  Archive files are not touched. Back up first.

Under a running job's progress bar you see how many items are done out of the total and,
after a few seconds, an estimate of the time left. The log of the job you started last shows
at the bottom of the page.

## Settings and accounts

- **Account** — your colour scheme (light, dark, or follow the system).
- **Library** (changed by administrators, shown to everyone):
  - *Crawler* — thumbnails per archive, thumbnail size, parallel workers, crawl subfolders,
    store the list of STL files, AI image embeddings, compute embeddings during crawls;
  - *Warnings* — which warnings crawls raise, the low-compression threshold, and the file
    name patterns that mark presupported STLs;
  - *Metadata sidecars* — write `METADATA.7z` into archive folders, with or without
    thumbnails;
  - *Archive files* — allow *Sort files*, allow *Delete files*, and the trash folder deleted
    archives go to;
  - *AI tagger* — the temperature (lower keeps suggestions literal).
- **Users** (administrators) — add users with a role, change roles, set a new password,
  disable or delete accounts.

## Path patterns

A path pattern describes how your folders are named, so crawls and *Mass edit* can fill fields
from them, and *Sort files* can build folders from fields. Each `{token}` matches one folder
level (or the file name for the last one); `*` matches a level to ignore.

| Token | Aliases | Field |
|---|---|---|
| `{source}` | | Type (shop or platform) |
| `{creator}` | `{author}` | Creator |
| `{title}` | `{name}` | Title |
| `{date}` | `{month}` | Date (`YYYY-MM` or `YYYY`) |
| `{sculptor}` | | Sculptor |
| `{collection}` | `{category}` | Collection |
| `{subcollection}` | `{subcategory}` | Subcollection |
| `{release}` | | Release |
| `{sequence}` | | Position in a series |
| `{url}` | | URL |

Example: archives stored as `ArtistName/2024-01 Some Release/model_name.zip` use
`{sculptor}/{release}/{title}`. In *Sort files*, `{token:n}` keeps the first *n* characters:
`{creator:1}/{creator}/{title}` puts creator *Sanix* under `S/Sanix/`.

## Keyboard and mouse

| Where | Action |
|---|---|
| Table | Click selects; Ctrl-click adds/removes; Shift-click selects a range; double-click a cell edits it |
| Cell editor | Enter saves; Esc cancels |
| Image viewer | ← / → previous and next image; Esc closes |
| Filter pane | Click a value: include → exclude → off; click a header's circle: has value → no value → off |
| Desktop app | Ctrl+L back to the start page (switch server); Ctrl+R reload; Ctrl+Q quit |

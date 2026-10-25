// The in-app help (Help page): every topic as data, so the page can search
// it and render it one way. Text may use **bold**, `code` and links to other
// topics written [label](topic-id).
import splash from "./splash.jpg";

export type Block =
  | { p: string }
  | { list: string[] }
  | { steps: string[] }
  | { table: { head: string[]; rows: string[][] } }
  | { tip: string }
  | { warn: string }
  | { h: string }
  | { img: string; alt: string };

export interface Topic {
  id: string;
  group: string;
  title: string;
  /** One line under the title and in search results. */
  summary: string;
  blocks: Block[];
}

export const GROUPS = ["Getting started", "Collecting", "Browsing and editing", "Tools", "AI", "Maintenance", "Administration", "Reference"] as const;

export const TOPICS: Topic[] = [
  // ── Getting started ──────────────────────────────────────────────────────
  {
    id: "overview",
    group: "Getting started",
    title: "What StlArchivist is",
    summary: "A catalogue of your 3D-printable model archives, read where they are.",
    blocks: [
      { img: splash, alt: "The archivist with a model in his hand" },
      { p: "StlArchivist keeps a catalogue of your 3D-printable model archives (`.zip`, `.7z`, `.rar`): titles, creators, sculptors, collections, tags, scales and preview images. It reads the archives where they are — nothing is unpacked, copied or changed unless you ask for it (Sort files, Delete files, sidecars)." },
      { h: "How it is built" },
      { list: [
        "**The server** (`stl-server`) owns the library: the model database `models.db`, the thumbnail store `thumbnails.lmdb/` and the settings, all in one **data directory**. It does the heavy work (crawls, thumbnails, AI) as background [jobs](jobs).",
        "**The web client** — these pages — runs in any browser and talks to the server. Several people can use one server at the same time, each with their own account and [role](roles).",
        "**The desktop app** shows the same pages in a window. It can open a library on your computer (it starts a private server for that window) or connect to a server elsewhere. Inside the app a few extra things work: showing an archive in the file manager, and [using the computer for AI](compute-workers).",
      ] },
      { h: "Characteristics" },
      { list: [
        "**Fast re-crawls** — archives are recognised by a fingerprint; unchanged ones are skipped and moved ones keep their record, edits and images.",
        "**Metadata from everywhere** — embedded `info.json` and `.url` files, the folder names through [path patterns](path-patterns), and the [AI tagger](ai-tagger).",
        "**Checks** — scales (28mm, 32mm, 1/35…), presupported files (Lychee, Chitubox, name patterns) and [warnings](warnings) for archives without images, without printable files, with nested archives or that barely compress.",
        "**Live updates** — jobs report progress to every open page, and what one person edits shows up for the others.",
        "**Compatible** — the data directory is the same one the earlier stlLibrary desktop version (the program's former name) used.",
      ] },
      { tip: "A library can be open in one program at a time. A second server, or the desktop app, on the same data directory is refused." },
    ],
  },
  {
    id: "getting-started",
    group: "Getting started",
    title: "Getting started",
    summary: "Open a library, sign in, and catalogue your first folder.",
    blocks: [
      { h: "Open a library" },
      { list: [
        "**Desktop app, library on this computer** — under **This computer** choose the library folder and click **Open**. A new, empty folder starts a new library; an existing one opens as it is.",
        "**Desktop app or browser, shared server** — in the desktop app add the server's address (for example `http://nas:8770`) under **Servers** and click **Connect**; in a browser, open that address.",
      ] },
      { h: "First time" },
      { p: "A new library asks for an administrator account (a name and a password of at least 8 characters). Afterwards everyone signs in with their own account; administrators add users on **Settings → Users**." },
      { h: "Your first catalogue" },
      { steps: [
        "Open **Libraries** and click **Add library**; browse to the folder holding your archives and click **Use this folder**. The folder becomes a [library](libraries).",
        "Optionally give a [path pattern](path-patterns) such as `{sculptor}/{release}/{title}`, so the folder names fill those fields.",
        "Click **Crawl** and follow the job on the right. You can keep working meanwhile.",
        "Open **Library** to browse, filter and edit what was found.",
        "Turn on [monitoring](monitored-folders) (the eye button) to have the folder kept up to date by itself.",
      ] },
      { tip: "What you can do depends on your role: viewers browse, editors also edit, crawl and run maintenance, administrators also manage users, settings, components and backups. Buttons you cannot use are hidden or disabled." },
    ],
  },
  {
    id: "pages",
    group: "Getting started",
    title: "The pages",
    summary: "What each page of the top bar is for.",
    blocks: [
      { table: { head: ["Page", "For"], rows: [
        ["**Library**", "Browsing, searching, filtering and editing models; the AI tagger, mass edit and archive actions on a selection"],
        ["**Folders**", "Browsing archives folder by folder, as thumbnails"],
        ["**Find by Image**", "Models whose pictures look like one you drop"],
        ["**Duplicates**", "Archives sharing files or images; models with similar metadata"],
        ["**Lookups**", "Tags, categories, collections, subcollections, scales and creators"],
        ["**Libraries**", "Libraries and their folders: add, crawl, monitor, rename, move, remove"],
        ["**Statistics**", "Totals, rankings and the distribution of any column"],
        ["**Settings**", "Your account, the library's settings, and tabs for maintenance, sidecars, backups, compute workers, components and users"],
        ["**Jobs**", "Every background job with its log; the button appears at the end while jobs wait or run (the status bar opens it at any time)"],
      ] } },
      { p: "The menu under your name (top right) has **Help**, **About**, **Change password** and **Sign out**. The **?** button opens this help." },
      { p: "The **status bar** at the bottom of every page shows the running [job](jobs) with its progress and how many wait behind it (click it to open **Jobs**), and how many archives all [libraries](libraries) hold." },
    ],
  },

  // ── Collecting ───────────────────────────────────────────────────────────
  {
    id: "crawling",
    group: "Collecting",
    title: "Crawling folders",
    summary: "How archives get into the library, and how re-crawls stay fast.",
    blocks: [
      { p: "A **crawl** reads a folder of archives (and its subfolders, unless *Crawl subfolders* is off in the settings) and catalogues every archive: its file list, preview images, `info.json` and `.url` files, scales and presupports, and the fields a [path pattern](path-patterns) takes from the folder names." },
      { h: "Crawling a folder" },
      { steps: [
        "**Libraries → Add library**, browse to the folder, **Use this folder** (or crawl an existing [library](libraries) with its ▶ button).",
        "In *Crawl folder* optionally enter a path pattern. Tick **Force rescan** only to re-read archives that have not changed (after changing the warning settings, for example).",
        "**Crawl**. The job's progress and log appear on the right.",
      ] },
      { h: "What a crawl does, step by step" },
      { list: [
        "**Missing records** — records whose archive is no longer on disk are noted first, so they can be matched if the archive turns up elsewhere.",
        "**Fingerprints** — each archive gets a fingerprint from its contents' CRCs, without decompressing it. An archive whose size and date match its record is not even read again (its size alone, with *Detect changed archives by size only* in the library settings: for drives or sync tools that change dates without changing files; an archive changed in place to the very same size then needs Rescan).",
        "**New and changed archives** are read in parallel (*Parallel workers* setting): file list, images (up to *Thumbnails per archive*, at *Thumbnail size*), metadata, scales, presupports and warnings. With embeddings during crawls on, their [AI embeddings](embeddings) are computed too.",
        "**Moved archives** — an archive with a known fingerprint at a new path takes over its old record, keeping its edits, tags and images.",
        "**Gone archives** — records of archives that vanished from the library are deleted (or [marked missing](monitored-folders) by monitored crawls); ones elsewhere are marked missing. Libraries inside the crawled one are left to their own crawls.",
        "**Sidecars** — with [sidecars](sidecars) on, each folder's `METADATA.7z` is imported first (restoring records without reading the archives) and written afterwards.",
      ] },
      { h: "The libraries list" },
      { list: [
        "**Update all** re-crawls every library with its saved pattern, matches archives moved between libraries, then removes the records of archives that are gone.",
        "Each library can be renamed, given another folder, or removed; each of its folders has buttons to **monitor** it, **crawl** it, [move it](libraries) to another place, or remove it.",
        "Editors can only choose folders the administrator made available (`roots`); administrators can choose any folder on the server.",
      ] },
      { warn: "In the desktop app's *This computer* mode the server belongs to the window: closing the window stops a running crawl. The next crawl or Update all continues from there." },
    ],
  },
  {
    id: "libraries",
    group: "Collecting",
    title: "Libraries",
    summary: "Named, movable collections of one or more folders: records store paths relative to their folder.",
    blocks: [
      { p: "A **library** is a named collection of archives that StlArchivist catalogues, made of one or more **folders** — say your miniatures on two disks, `D:\\STL` and `E:\\More STL`, as one *Miniatures* library. Each folder has a **root** (where it is on the server), its own [path pattern](path-patterns) for crawls, and a permanent identity. Every record stores its archive's path **relative to its folder** — `Studio/2024-01 Release/Dragon.zip`, not `D:\\STL\\Studio\\…` — in a form that is the same on Windows, Linux and macOS." },
      { p: "That makes a library **movable**: copy a folder to another disk, mount it elsewhere, move the whole collection to a new server, or open a Windows library on Linux — then point the folder at its new place. Records, tags, edits, thumbnails and AI embeddings stay; nothing is re-read." },
      { h: "Managing libraries" },
      { list: [
        "The **Libraries** page lists each library with its record count, and under it its folders with their state: *root not found* (the folder is gone or not mounted), *monitored*, or *made by the move to libraries*.",
        "**Add library** picks a folder, makes it a new library and offers to crawl it. Crawling a folder that is in no library yet makes it one; crawling a sub-folder of a library's folder keeps it in that library.",
        "**Add folder** (the folder button on a library) adds another folder to that library and offers to crawl it. A folder belongs to one library.",
        "**Rename** changes only the library's name (a name another library has gets a number).",
        "**Move to another library** (the two-arrows button on a folder) takes the folder with its records — edits, tags and thumbnails stay, nothing is re-read — to another library or a new one; a library left without folders goes.",
        "Each folder can be **monitored**, **crawled**, [moved](libraries) or **removed** on its own: removing a folder sends its records to the folder around it, if one contains it, or deletes them; a library left without folders goes too.",
        "**Remove library** removes all its folders the same way. The archive files are never touched.",
        "In the browser, the **Library** filter section and column show which library a model is in; the **Path** section lists each library, then its folders, then their sub-folders; the **Path** column shows the full path on the server.",
      ] },
      { h: "Moving a folder" },
      { steps: [
        "Move or copy the folder (or mount it at its new place).",
        "On the folder, click **Move** (the arrows button), choose the **new folder** and click **Check**: every archive is looked up there, and you see how many were found, missing or found only with another letter case.",
        "Click **Move**. The folder now lives in its new place; folders inside it move along. The library's other folders are not affected.",
      ] },
      { p: "Records whose archive was not found stay, and the next crawl marks them missing. *Adopt the letter case found on disk* helps after moving from Windows (which ignores letter case) to Linux (which does not). On a headless server the same is `stl-server libraries relocate <folder> <new folder> --apply` (`stl-server libraries list` shows the folders' names and ids)." },
      { h: "Folders inside folders" },
      { p: "A folder may sit inside another one — of the same library or of another, a sub-collection with its own pattern. An archive belongs to the **deepest** folder above it: adding an inner folder takes over the records below it, crawling the outer one skips the inner folder, and removing the inner one gives its records back." },
      { h: "Moving to another machine" },
      { steps: [
        "Back up on the old machine (**Settings → Backups**) and copy the archives.",
        "Restore on the new one (`stl-server restore`) and open it: its folders show *root not found*.",
        "Move each folder to its place on the new machine.",
      ] },
      { tip: "Libraries came with version 2026.43.7. Opening an older library converts it once: each crawled folder becomes a library, archives outside every crawled folder get a library of their own, and a backup (`…_pre_libraries.7z`) is made first. The earlier desktop application cannot open the converted library." },
    ],
  },
  {
    id: "monitored-folders",
    group: "Collecting",
    title: "Monitored folders",
    summary: "Folders the server checks for new, changed or removed archives and crawls by itself.",
    blocks: [
      { p: "A monitored folder keeps itself up to date: drop new archives into it, replace or delete some, and the library follows without anyone starting a crawl." },
      { h: "Turning it on" },
      { p: "On the **Libraries** page, click the **eye** button of a library's folder (editors). Monitored folders show a *monitored* badge; click the eye again to stop." },
      { h: "How it works" },
      { list: [
        "Every *Check monitored folders every* minutes (**Settings → Library**, default 30) the server lists the folder's archives and compares them with the library **by file size and modification date**. No archive is opened, so checking an unchanged folder is cheap, even on a NAS.",
        "Only when an archive was **added, changed or removed** does it queue an ordinary crawl of the folder, shown in **Jobs** as submitted by `monitor`. Nothing is queued when nothing changed, or while that folder's crawl or Update all is already waiting or running.",
        "The crawl uses the folder's saved path pattern and adds or updates records like any crawl.",
        "Archives that vanished are **marked missing** rather than deleted, so a mistake or a disconnected share does not lose your edits. An archive that turns up in another library is matched to its record by content.",
        "**Settings → Maintenance → Remove missing archives** deletes the records of every missing archive when you are sure.",
      ] },
      { tip: "Changes are found at the next check, not instantly. That also works on network shares and in Docker, where file-change notifications often do not. Changes made while the server was off are found at the first check after it starts." },
    ],
  },
  {
    id: "path-patterns",
    group: "Collecting",
    title: "Path patterns",
    summary: "Describe how your folders are named so fields fill themselves.",
    blocks: [
      { p: "A path pattern tells crawls and Mass edit how to read fields from folder names, and tells Sort files how to build folders from fields. Each `{token}` matches one folder level (the last one the file name); `*` matches a level to ignore." },
      { table: { head: ["Token", "Aliases", "Field"], rows: [
        ["`{source}`", "", "Type (shop or platform)"],
        ["`{creator}`", "`{author}`", "Creator"],
        ["`{title}`", "`{name}`", "Title"],
        ["`{date}`", "`{month}`", "Date (`YYYY-MM` or `YYYY`)"],
        ["`{sculptor}`", "", "Sculptor"],
        ["`{collection}`", "`{category}`", "Collection"],
        ["`{subcollection}`", "`{subcategory}`", "Subcollection"],
        ["`{release}`", "", "Release"],
        ["`{sequence}`", "", "Position in a series"],
        ["`{url}`", "", "URL"],
        ["`*`", "", "A level to ignore"],
      ] } },
      { h: "Examples" },
      { list: [
        "`ArtistName/2024-01 Some Release/model_name.zip` → `{sculptor}/{release}/{title}`.",
        "`Patreon/Studio/2023-05/Dragon.zip` → `{source}/{creator}/{date}/{title}`.",
        "`Fantasy/whatever/Knight.7z` → `{collection}/*/{title}`.",
      ] },
      { p: "In Sort files, `{token:n}` keeps the first n characters: `{creator:1}/{creator}/{title}` puts creator *Sanix* under `S/Sanix/`." },
      { tip: "Use **Mass edit → Path pattern → Preview** to try a pattern on existing models before saving anything." },
    ],
  },
  {
    id: "warnings",
    group: "Collecting",
    title: "Scales, presupports and warnings",
    summary: "What crawls detect in an archive and how to tune it.",
    blocks: [
      { h: "Scales" },
      { p: "Scales such as `28mm`, `32mm` or `1/35` are read from the archive's name, its folders and its file names. A model can have several; they are a filter and a column." },
      { h: "Presupports" },
      { p: "Presupported models are recognised from Lychee and Chitubox project files and from file names matching the presupport patterns (**Settings → Library → Warnings**). Patterns are regular expressions; a pattern the server cannot read the same way is skipped with a note in the crawl log." },
      { h: "Warnings" },
      { table: { head: ["Warning", "Raised when"], rows: [
        ["Presupported STL", "An STL looks presupported (by the patterns)"],
        ["No images", "The archive has no preview image, and none was found beside it in its folder"],
        ["Nested archive", "The archive contains another archive"],
        ["No printable files", "No printable model file inside"],
        ["Low compression", "The archive's compression rate is above the threshold (default 0.95)"],
      ] } },
      { p: "Which warnings are raised is set in **Settings → Library → Warnings**. Warnings show as orange badges in the details pane; **Warning details** lists the files behind each. After changing the settings, **Settings → Maintenance → Check images warning / Check presupport warning** fixes those warnings quickly without a full rescan." },
    ],
  },

  // ── Browsing and editing ─────────────────────────────────────────────────
  {
    id: "browsing",
    group: "Browsing and editing",
    title: "Browsing and filtering",
    summary: "Search, faceted filters, the table and grid, and the details pane.",
    blocks: [
      { p: "**Library** has the filter pane on the left, the models in the middle and the selected model's details on the right." },
      { h: "Search and filters" },
      { list: [
        "**Search** matches titles, creators, paths and tags.",
        "**Filter pane** — one section per field. Click a value to cycle it: **+** (green) only models with it, **−** (red) hide them, a third click clears it. Several values combine.",
        "The **circle** on a section's header cycles *has a value* / *has no value* / off — for example models without tags.",
        "Counts beside values show how many models each would match together with the other active filters. Long sections have their own search box. **Clear** removes every filter.",
      ] },
      { h: "Table and grid" },
      { list: [
        "Click a header to sort (ascending, descending, off); drag a header's edge to resize.",
        "**Columns** chooses the columns (saved with your account); *More columns* adds Warning, Sequence, Images, Compression, To print, Missing, Added, Updated, URL, Description, Folder and Archive hash.",
        "**Table / Images** switches to a thumbnail grid of the same models.",
        "Click selects; **Ctrl**-click adds or removes; **Shift**-click selects a range. The selection drives Mass edit, Archives and the AI tagger.",
      ] },
      { h: "Details pane" },
      { list: [
        "The model's fields, tags, warnings, path and images. Drag its left edge to resize (double-click resets).",
        "**Download** the archive; **Copy path**; **Open folder** (desktop app) shows it in the file manager. Paths are translated with the server's `path_map` so they make sense on your computer.",
        "**Files** shows the archive's folders and files as a tree, with each file's size and CRC-32 (from the archive's catalog: every crawl stores it, nothing is unpacked). The cube beside a model file opens it in the model viewer.",
        "**View model** shows the archive's 3D models (STL, OBJ, PLY, 3MF, Lychee `.lys`, CHITUBOX `.chitubox`, `.ctp` and `.ctb`) with MeshPeek's renderer. The files are a folder tree on the left: click a file to show it alone, tick files or folders (or **Ctrl**-click) to show several together, **↑**/**↓** step through them (in the tree, **Enter** shows the focused file and **Space** adds or takes it away). Files taken out of the view are kept for a while, so showing them again is instant. Right-click a file or folder for its colour, material (Matte, Plastic, Glossy, Metal), wireframe, **Centre on it**, **Information** and **Open in MeshPeek**. The ⓘ beside a shown file (or in the toolbar, for the current file) shows its information as MeshPeek does: path, triangles, support triangles, size in X, Y and Z, and what the file says about itself (format, file size, slicer, printer, layers…). **Spread out** (toolbar) puts the shown files side by side on the build plane instead of on top of each other. Editors can click the camera button (**Add this view to the model's images**) to make the shown models, as drawn and cropped to them, one of the model's images (at the library's *Model viewer captures* size).",
        "In the view: left drag orbits, middle or **Shift** drag pans, the wheel or right drag zooms, **F** frames everything, **W** switches the wireframe, **0**–**6** are the standard views; click a face of the view cube (top right) to look from that side, or drag the cube to turn the view. The buttons top left switch the wireframe, *Colour each model* and the build plate. Lychee and CHITUBOX scenes show their supports and build plate.",
        "**Open in MeshPeek** (desktop app, when the MeshPeek viewer is installed and the archive is reachable from your computer) opens the archive, or the file shown in the viewer, in MeshPeek. MeshPeek is looked for on the `PATH` and in Program Files; **Settings → Account → MeshPeek on this computer** chooses the program instead.",
        "Click an image for the full-resolution original read from the archive; **←**/**→** step through the images.",
      ] },
      { h: "Folders and Statistics" },
      { p: "**Folders** shows the folders holding catalogued archives as a tree (the box above filters it; folders without archives of their own are dimmed); click one to see its archives as thumbnails (**Include subfolders** adds those below). **Statistics** shows totals, the most frequent values per field and the distribution of any column." },
    ],
  },
  {
    id: "editing",
    group: "Browsing and editing",
    title: "Editing models",
    summary: "In-place edits, the model editor, mass edit and lookups.",
    blocks: [
      { p: "Editing needs the **editor** role. Edits reach everyone's open pages at once, and update the folder's [sidecar](sidecars) shortly after when sidecars are on." },
      { list: [
        "**In the table** — double-click a cell, type, **Enter** saves, **Esc** cancels. Tags and scales are comma-separated.",
        "**Model editor** — **Edit** in the details pane: every field, with suggestions from the library.",
        "**Images** — drop image files on the details pane to add them; the × on an image removes it.",
        "**Remove from library** — removes the selected records and their images from the catalogue; the archive files stay, and a crawl brings them back.",
      ] },
      { h: "Mass edit" },
      { p: "Select several models, then **Mass edit**:" },
      { list: [
        "**Path pattern** takes values from each model's folders; **Preview** shows what it finds for every model before saving.",
        "**Fields** sets values for all of them; **Clear** empties a field. With *replace* on, values replace existing ones; otherwise they only fill empty fields. Scales and tags are added.",
        "**Find & replace** changes text in the chosen fields (case-sensitive or regular expression if you want); **Trim** removes stray spaces.",
      ] },
      { h: "Lookups" },
      { p: "The **Lookups** page manages tags, categories, collections, subcollections and scales: rename, delete (it says how many models lose the value first), merge two entries, and delete unused tags. The **Creators** tab renames, merges and clears creators and edits their profile (links to Patreon, ArtStation, Gumroad, MyMiniFactory)." },
    ],
  },
  {
    id: "archives",
    group: "Browsing and editing",
    title: "Working with archive files",
    summary: "Rescan, regenerate thumbnails, sort files into folders, delete files.",
    blocks: [
      { p: "Select models, then open **Archives** above the table. These run as [jobs](jobs)." },
      { list: [
        "**Rescan** re-reads the archives (metadata, file list, presupports, warnings, scales) — after replacing an archive's contents in place. Right-clicking a row or image offers it too: for the selection when the row is part of it, else for that model.",
        "**Generate thumbnails** replaces the models' images with new ones from their archives, up to *Thumbnails per archive*.",
        "**Sort files…** moves the archives into a folder tree built from a [pattern](path-patterns) under a target folder. **Preview** shows every move first (*COLLISION*: a file of that name exists and a numbered name is used; *MISS*: the archive is not on disk). **Move** moves all or none: if one move fails, the others are put back. Needs *Allow file sorting* in the settings.",
        "**Delete files…** deletes the archive files and their records. With a trash folder set, files go there with a small `.json` note for recovery. Needs *Allow file delete* in the settings.",
      ] },
      { warn: "Sort files and Delete files change your disk. Preview first, and keep backups of archives you cannot download again." },
    ],
  },

  // ── Tools ────────────────────────────────────────────────────────────────
  {
    id: "duplicates",
    group: "Tools",
    title: "Duplicates and Find by Image",
    summary: "Spot the same model under different names, files or renders.",
    blocks: [
      { h: "Find by Image" },
      { p: "Drop a picture (or click to choose one). **Perceptual hash** finds near-identical images; the slider allows more differing bits. **AI embedding** (when [embeddings](embeddings) are available) finds the same model in other renders, angles and colours; the slider sets the minimum similarity. Results update as you move the slider." },
      { h: "Duplicates" },
      { p: "Three searches, each run as a job; the newest result shows again when you come back:" },
      { list: [
        "**STL files** — archive pairs sharing at least the chosen share of their files (same name and size).",
        "**Images** — archive pairs with matching images, by perceptual hash or AI embedding. *Skip images in more than N models* ignores banners and logos that appear everywhere.",
        "**Metadata** — models with equal (*Exact*), almost equal (*Levenshtein*, up to N edits) or similar-sounding (*Soundex*) values in the chosen columns.",
      ] },
      { p: "**Compare** opens two models side by side. Filter the results with the box above them." },
    ],
  },

  // ── AI ───────────────────────────────────────────────────────────────────
  {
    id: "ai-tagger",
    group: "AI",
    title: "AI tagger",
    summary: "A vision model suggests tags and fields from the images and names.",
    blocks: [
      { p: "The AI tagger uses Qwen2.5-VL (through llama.cpp) to look at a model's thumbnails, archive path, file names and catalogued metadata, and suggests tags, title, creator, sculptor, collection, scale, source, year and description." },
      { h: "Using it" },
      { list: [
        "Select models, then **AI tagger**. If the model is not available the drawer says what is missing.",
        "**Review one by one** — **Suggest** asks about the current model (the first request also loads the model, which takes a while). Edit the tags and fields, then **Save & next** or **Skip**.",
        "**Process all** — tags every selected model in one job and saves the results.",
        "Options: **Max tags**; **Ignore catalogued metadata** (images and names only); **Overwrite filled fields** (by default only empty fields are filled; tags are always added).",
      ] },
      { h: "Where it runs" },
      { list: [
        "On the server, when an administrator installed the runtime and the model (**Settings → Components**). The server starts the model on first use and stops it after ten idle minutes. The CUDA runtime uses an NVIDIA GPU.",
        "Or on a [compute worker](compute-workers): a desktop app on a computer with a GPU.",
      ] },
      { p: "*Settings → Library → AI tagger temperature* sets how literal suggestions are (lower is more literal; default 0.2)." },
      { tip: "The model's licence (Qwen Research License) limits commercial use; it must be accepted when downloading it." },
    ],
  },
  {
    id: "embeddings",
    group: "AI",
    title: "AI image embeddings (CLIP)",
    summary: "Vectors that let Find by Image and Duplicates match models by what they look like.",
    blocks: [
      { p: "An embedding is a vector describing what an image shows. Two renders of the same model in other colours or angles get similar vectors, so **Find by Image** and **Duplicates → Images** can find them where a perceptual hash would not." },
      { h: "Setting it up" },
      { steps: [
        "An administrator downloads **ONNX Runtime** and the **CLIP image encoder** in **Settings → Components** (or a [compute worker](compute-workers) provides them).",
        "**Settings → Maintenance → Compute missing embeddings** computes the vectors of all thumbnails.",
        "Optionally turn on *Compute embeddings during crawls* in **Settings → Library**, so new archives get theirs at once.",
      ] },
      { h: "GPU acceleration" },
      { list: [
        "ONNX Runtime comes in variants: **CPU**, **GPU (DirectML)** on Windows (any DirectX 12 GPU) and **NVIDIA GPU (CUDA)** on Linux (CUDA 12 and cuDNN 9 must be installed). The components card preselects what fits the GPUs it finds.",
        "The encoder runs on the GPU when the installed build supports it, and falls back to the CPU otherwise. The components card shows where it runs (*CLIP encoder runs on DirectML*).",
        "On Linux the server detects the GPU vendor and tries the matching provider: CUDA for NVIDIA, MIGraphX or ROCm for AMD, OpenVINO for Intel (the last two need a vendor build of ONNX Runtime).",
        "GPU and CPU give the same vectors, so switching never makes old embeddings incomparable. A GPU is several times faster (an RTX 3070: about 2 ms per image instead of 18).",
      ] },
      { tip: "Changing the ONNX Runtime variant needs a server restart if it was already loaded." },
    ],
  },
  {
    id: "compute-workers",
    group: "AI",
    title: "Compute workers",
    summary: "Let a computer with a GPU do the AI work of a small server.",
    blocks: [
      { p: "A server on a small machine (a NAS, a mini PC) can hand CLIP embeddings and the AI tagger to a computer that has a GPU. Jobs, progress, reviews and the database stay on the server; only images and prompts travel to the worker, and vectors and replies come back." },
      { h: "Making this computer a worker" },
      { steps: [
        "On the computer with the GPU, open the server in the **desktop app** and go to **Settings → Workers**.",
        "Under **This computer**, choose where the models go with **Change folder…** (or keep the default), tick the components (ONNX Runtime with DirectML, the CLIP encoder, the AI runtime with CUDA, the AI model) and **Download**. Progress shows below the list.",
        "Turn on **Use this computer for AI**.",
      ] },
      { p: "The worker connects to the server by itself (outgoing, so it works behind a router and through a reverse proxy), starts again whenever the app connects to that server, and stops when the app closes. It pauses while components are installed." },
      { h: "On the server" },
      { list: [
        "The card lists the connected workers, what each does (*CLIP on DirectML · AI tagger*) and how many requests it answered.",
        "A connected worker is always preferred. Without one, a server with `\"compute\": \"local\"` (default) uses its own components; with `\"compute\": \"workers\"` (or `STL_COMPUTE=workers`) it never runs the models: jobs **wait** for a worker, crawls skip embeddings, and Find by Image asks for a worker.",
        "A worker's CLIP encoder is used only when it is the same model as the library's, so all vectors stay comparable.",
      ] },
      { tip: "The worker uses a session of the user who turned it on (an editor). Turning the switch off, or disabling that user, ends it." },
    ],
  },

  // ── Maintenance ──────────────────────────────────────────────────────────
  {
    id: "maintenance",
    group: "Maintenance",
    title: "Maintenance",
    summary: "Checking and repairing the library (Settings → Maintenance).",
    blocks: [
      { p: "Maintenance lives in **Settings**: the **Maintenance** tab checks and repairs records, warnings, image hashes and embeddings; **Sidecars**, **Backups** (administrators) and **Workers** have tabs of their own." },
      { h: "Records" },
      { list: [
        "**Verify archives** checks that every archive is still on disk and unchanged in size, and lists the problems (*Missing*, *Flagged Missing*, *Modified*).",
        "**Find duplicate entries** lists records that catalogue the same archive twice.",
        "On that list: **Find moved in DB** looks for each missing record's archive among the other records; **Merge moved** moves its metadata there. **Search in folder…** looks for missing archives in a folder you pick and points the records at them. **Remove selected** / **Remove all missing** delete records. Actions apply to ticked rows, or to all when none is ticked.",
        "**Remove missing archives** deletes the records of every archive marked missing — for example those [monitored folders](monitored-folders) marked. Files are not touched.",
        "**Prune images** keeps at most N images per model; **Compact database** (administrators) reclaims space after large deletions: the database and the thumbnail store are rewritten without their free pages, and thumbnails no image uses any more are dropped (thumbnails wait while the store is rewritten; each file needs room for a copy on its drive).",
      ] },
      { h: "Warnings, hashes and embeddings" },
      { list: [
        "**Check images warning** / **Check presupport warning** re-read every archive's file list and fix just that [warning](warnings), much faster than a rescan.",
        "**Compute missing (or all) image hashes** for perceptual-hash duplicates; **Compute missing embeddings** for [AI similarity](embeddings).",
      ] },
      { h: "Other cards" },
      { list: [
        "**Metadata sidecars** — see [sidecars](sidecars).",
        "**Compute workers** — see [compute workers](compute-workers).",
        "Downloads of components are in **Settings → Components** — see [components](components).",
        "**Backups** (administrators) — see [backups](backups).",
        "**Delete all records** (administrators) — empties the library after you type `DELETE`. Archive files are not touched. Back up first.",
      ] },
      { p: "Under a running job's progress bar you see how many items are done and, after a few seconds, the time left. The log of the job you started last shows at the bottom." },
    ],
  },
  {
    id: "sidecars",
    group: "Maintenance",
    title: "Metadata sidecars",
    summary: "A METADATA.7z per folder that carries your edits with the archives.",
    blocks: [
      { p: "With sidecars on (**Settings → Library → Metadata sidecars**), the server keeps a `METADATA.7z` in every archive folder with the records of its archives (and their thumbnails, if chosen). Copy the folder to another disk or computer and a new library restores titles, tags and edits when it crawls it — without reading the archives again." },
      { list: [
        "Edits update the folder's sidecar shortly afterwards.",
        "**Generate sidecars** writes them in every folder now.",
        "**Clean sidecars** rewrites ones listing other archives than the folder's records, deletes ones whose archives are all gone, and removes leftover `.tmp` files.",
        "**Remove all sidecars** deletes them.",
      ] },
      { tip: "Sidecars need write access to the archive folders. Mount archives read-only if you do not want them." },
    ],
  },
  {
    id: "jobs",
    group: "Maintenance",
    title: "Jobs",
    summary: "Background work: the queue, progress, logs and stopping.",
    blocks: [
      { p: "Crawls, rescans, maintenance, duplicate searches, the AI tagger's batches and downloads run on the server as **jobs**. You can leave the page, close the browser or keep working." },
      { list: [
        "Jobs run **one at a time**; new ones wait their turn. Every open page sees their progress live.",
        "**Jobs** lists them with who started them (`monitor` for [monitored folders](monitored-folders)); click one for its log.",
        "The **stop** button stops a job at its next safe point, keeping the work already done.",
        "Progress shows items done out of the total and, after a few seconds, an estimate of the time left.",
      ] },
    ],
  },

  // ── Administration ───────────────────────────────────────────────────────
  {
    id: "roles",
    group: "Administration",
    title: "Users and roles",
    summary: "Who can do what.",
    blocks: [
      { table: { head: ["", "Viewer", "Editor", "Administrator"], rows: [
        ["Browse, search, filter, download", "✓", "✓", "✓"],
        ["Edit models, mass edit, lookups, AI tagger", "", "✓", "✓"],
        ["Crawl, monitor folders, maintenance jobs", "", "✓", "✓"],
        ["Folders inside the allowed roots", "", "✓", "✓"],
        ["Any folder on the server", "", "", "✓"],
        ["Use this computer for AI (compute worker)", "", "✓", "✓"],
        ["Library settings, users, components, backups, compacting, deleting all", "", "", "✓"],
      ] } },
      { p: "Administrators manage accounts on **Settings → Users**: add users with a role, change roles, set a new password, disable or delete accounts. Everyone changes their own password from the menu under their name." },
    ],
  },
  {
    id: "settings",
    group: "Administration",
    title: "Settings",
    summary: "Your account and the library's settings.",
    blocks: [
      { list: [
        "**Account** — your colour scheme (light, dark, or follow the system) and accent colour (buttons, selections and highlights); in the desktop app also the MeshPeek program **Open in MeshPeek** runs.",
        "**Library** (administrators change, everyone sees):",
      ] },
      { table: { head: ["Section", "Settings"], rows: [
        ["Crawler", "Thumbnails per archive, thumbnail size, the size of model viewer captures (the thumbnail size unless set), parallel workers, detect changed archives by size only, check monitored folders every N minutes, crawl subfolders, AI image embeddings, compute embeddings during crawls (the list of STL files is always stored)"],
        ["Warnings", "Which warnings crawls raise, the low-compression threshold, presupport file-name patterns"],
        ["Metadata sidecars", "Write METADATA.7z into archive folders, with or without thumbnails"],
        ["Archive files", "Allow Sort files, allow Delete files, the trash folder"],
        ["AI tagger", "Temperature"],
      ] } },
      { p: "Library settings live in the data directory (`settings.json`) and apply to everyone." },
    ],
  },
  {
    id: "components",
    group: "Administration",
    title: "Components",
    summary: "Downloading the optional parts: AI runtime and model, CLIP, ONNX Runtime, pdfium, UnRAR.",
    blocks: [
      { p: "**Settings → Components** is the one place for downloads. *Server components* (administrators) go into the library's folder, from their official sources, each file checked by its checksum (UnRAR on Windows by its publisher's signature). In the desktop app, *This computer* lists the components of this computer's [compute worker](compute-workers) — unless the worker uses the library's own folder (a library opened on this computer), when the server's list serves both and is shown once." },
      { table: { head: ["Component", "Enables", "Variants"], rows: [
        ["AI tagger runtime (llama.cpp)", "AI tagger", "CPU, NVIDIA GPU (CUDA, Windows)"],
        ["AI tagger model (Qwen2.5-VL, 3.3 GB)", "AI tagger", "—"],
        ["CLIP image encoder", "AI image similarity", "—"],
        ["ONNX Runtime", "AI image similarity", "CPU, GPU (DirectML, Windows), NVIDIA GPU (CUDA, Linux)"],
        ["pdfium", "PDF previews as thumbnails", "—"],
        ["UnRAR (RARLab)", "Compressed `.rar` archives: thumbnails, metadata, Rescan", "—"],
      ] } },
      { list: [
        "The card shows the GPUs found and preselects the fitting variants. Choosing another variant of an installed component replaces it.",
        "States: *installed*, *not installed*, *configured elsewhere* (the server's configuration points at another copy; not managed here), *not offered here* (no download for this platform).",
        "An interrupted download resumes where it stopped. **Remove selected** deletes downloaded components.",
        "UnRAR goes to `tools/` in the library's folder and is used whenever `unrar` is not set in `server.json` (or `STL_UNRAR_PATH`). On Linux it is version 7.23; on Windows RARLab offers only its latest UnRAR, which is checked by its signature (win.rar GmbH) and unpacks itself.",
      ] },
    ],
  },
  {
    id: "backups",
    group: "Administration",
    title: "Backups and restore",
    summary: "Saving and restoring the database, thumbnails and settings.",
    blocks: [
      { p: "**Settings → Backups** (administrators): **Back up now** saves the database, thumbnails and library settings in one `.7z` in `<data dir>/backups`; download or delete backups from the list." },
      { p: "Restoring is done on the server with the library closed (stop the server or close the desktop app):" },
      { p: "`stl-server restore <backup>.7z --data-dir <data dir>`" },
      { p: "The files it replaces are kept in `pre-restore-<time>` in the data directory. Backups made by the earlier desktop version restore the same way." },
    ],
  },
  {
    id: "server",
    group: "Administration",
    title: "Server configuration",
    summary: "server.json settings an administrator may need.",
    blocks: [
      { p: "The server reads `<data dir>/server.json` (all keys optional), then environment variables, then the command line." },
      { table: { head: ["Setting", "Environment", "Purpose"], rows: [
        ["`bind`", "`STL_BIND`", "Address and port (default `127.0.0.1:8770`)"],
        ["`roots`", "", "Folders editors may browse and crawl"],
        ["`path_map`", "", "How clients see server paths, for Copy path and Open folder"],
        ["`compute`", "`STL_COMPUTE`", "`local` or `workers` (see compute workers)"],
        ["`unrar`", "`STL_UNRAR_PATH`", "UnRAR for compressed `.rar` members (default: the downloaded component, else `unrar` on `PATH`)"],
        ["`pdfium` / `onnxruntime`", "`STL_PDFIUM_PATH` / `ORT_DYLIB_PATH`", "Libraries outside the data directory"],
        ["`ai`", "`STL_AI_URL`", "The AI tagger's model server, or a running one to use"],
        ["`components_mirror`", "", "Download components from your own mirror"],
      ] } },
      { tip: "Put the server behind an HTTPS reverse proxy before exposing it beyond your machine. The deployment guide covers Linux, Docker, Windows services and HTTPS." },
    ],
  },

  // ── Reference ────────────────────────────────────────────────────────────
  {
    id: "keyboard",
    group: "Reference",
    title: "Keyboard and mouse",
    summary: "Shortcuts in the table, viewer, filters and desktop app.",
    blocks: [
      { table: { head: ["Where", "Action"], rows: [
        ["Table", "Click selects; Ctrl-click adds/removes; Shift-click selects a range; double-click a cell edits it"],
        ["Cell editor", "Enter saves; Esc cancels"],
        ["Image viewer", "← / → previous and next image; Esc closes"],
        ["Filter pane", "Click a value: include → exclude → off; a header's circle: has value → no value → off"],
        ["Desktop app", "Ctrl+L start page (switch server); Ctrl+R reload; Ctrl+Q quit"],
      ] } },
    ],
  },
  {
    id: "troubleshooting",
    group: "Reference",
    title: "Troubleshooting",
    summary: "Common problems and what to do.",
    blocks: [
      { table: { head: ["Problem", "What to check"], rows: [
        ["A crawl finds 0 archives", "The folder as the server sees it (in Docker, the container path such as `/library/...`), and that the server's user can read it. Editors can only crawl inside the allowed roots."],
        ["The library is refused (already open)", "Another server or the desktop app has the same data directory open; close it."],
        ["No AI options, or AI tagger unavailable", "The components are missing (Settings → Components), or with `compute: workers` no worker is connected. The AI tagger drawer says which."],
        ["Embeddings run on the CPU", "Install ONNX Runtime's GPU variant (DirectML or CUDA) and restart the server; on Linux CUDA 12 and cuDNN 9 are needed."],
        ["The compute worker stopped", "The card shows why: no components in its folder (download them under This computer), or its session was ended (turn the switch on again)."],
        ["Jobs wait forever", "With `compute: workers`, AI jobs wait for a worker; connect one or stop the job."],
        ["Open folder / Copy path do not work", "The server's paths mean nothing on your computer: an administrator sets `path_map`."],
        ["Monitored folder not updated", "Changes are found at the next check (Settings → Library interval); the library's root must be reachable."],
        ["A library shows *root not found*", "Its folder moved, was renamed or is not mounted: mount it again, or use [Move](libraries) to point the library at its new folder."],
        ["Missing records pile up", "Settings → Maintenance → Remove missing archives, after checking they are really gone."],
      ] } },
    ],
  },
];

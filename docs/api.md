# StlArchivist API

Version 2026.43.20. Generated from `web/openapi.json` by `tools/api_doc.py`; the
running server serves the same document at `GET /api/openapi.json`. Do not edit by hand:
run `npm run gen:api` (in `web/`) and then `python tools/api_doc.py` after changing routes.

## Conventions

- Every route except `/api/auth/status`, `/api/auth/setup`, `/api/auth/login` and this document needs a session: `Authorization: Bearer <token>` or the `stl_session` cookie set at sign-in.
- Requests and responses are JSON. Request bodies refuse unknown fields (422).
- **Roles:** every signed-in user may read (viewer). Routes marked *editors* need the
  editor role, *administrators* the admin role; otherwise the answer is 403.
- **Errors** answer with an [ErrorBody](#errorbody) (`{"error": "…"}`) and a status:
  400 invalid request, 401 not signed in, 403 role too low, 404 not found, 409 conflict,
  422 a body that does not parse.
- Long work (crawls, maintenance, backups, AI batches) runs as a [job](#jobs-and-events):
  `POST /api/jobs` answers 202 with the job; follow it on `GET /api/events` (WebSocket)
  or `GET /api/jobs/{id}`.
- Writes that change the library are announced on `/api/events` as `library_changed`.

## Endpoints (82)

- [Sign-in and sessions](#sign-in-and-sessions) (5)
- [Users and preferences](#users-and-preferences) (7)
- [System](#system) (1)
- [Browsing the library](#browsing-the-library) (12)
- [Editing models](#editing-models) (11)
- [Lookups (sculptors, collections, tags…)](#lookups-sculptors-collections-tags) (13)
- [Thumbnails and images](#thumbnails-and-images) (3)
- [Files and folders](#files-and-folders) (2)
- [Libraries and their folders](#libraries-and-their-folders) (6)
- [Jobs and events](#jobs-and-events) (5)
- [Maintenance](#maintenance) (3)
- [Backups](#backups) (3)
- [Search](#search) (1)
- [Statistics](#statistics) (2)
- [Library settings](#library-settings) (2)
- [AI tagger](#ai-tagger) (2)
- [Compute workers](#compute-workers) (3)
- [Optional components](#optional-components) (1)
- [Types](#types)

### Sign-in and sessions

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/auth/login` | Sign in. After five failures a name is locked out for 15 minutes. | body: [Credentials](#credentials) | 200 [Session](#session) | 401, 429 |
| `POST /api/auth/logout` | End this session. |  | 204 |  |
| `PUT /api/auth/password` | Change your own password. Ends all your sessions, this one included. | body: [PasswordChange](#passwordchange) | 204 | 400 |
| `POST /api/auth/setup` | Create the first administrator and sign in. Only while no user exists, and only from the server's own machine (not through a proxy). | body: [Credentials](#credentials) | 200 [Session](#session) | 403, 409 |
| `GET /api/auth/status` | Whether setup is needed and who is signed in. Needs no session. |  | 200 [AuthStatus](#authstatus) |  |

### Users and preferences

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/me/settings` | Your own settings: key → any JSON value. |  | 200 object |  |
| `PUT /api/me/settings/{key}` | Store one of your settings (the body is its JSON value, at most 64 KB). | path `key`: string<br>body: object | 204 | 400 |
| `DELETE /api/me/settings/{key}` | Remove one of your settings. | path `key`: string | 204 |  |
| `GET /api/users` | Every user, by name. |  | 200 [User](#user)[] | 403 |
| `POST /api/users` | Create a user. | body: [NewUser](#newuser) | 201 [User](#user) | 409 |
| `PATCH /api/users/{id}` | Change a user's role, enabled state or password. The last enabled administrator cannot be demoted or disabled. | path `id`: integer<br>body: [UserChange](#userchange) | 200 [User](#user) | 404, 409 |
| `DELETE /api/users/{id}` | Delete a user (not the last enabled administrator). | path `id`: integer | 204 | 404, 409 |

### System

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/system/info` | Name, version and library size. |  | 200 [ServerInfo](#serverinfo) |  |

### Browsing the library

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/filter-tree` | Every value list and count the filter pane shows. | body: [FilterTreeRequest](#filtertreerequest) | 200 [FilterTree](#filtertree) | 400 |
| `GET /api/folders` | The folders holding catalogued archives (forward slashes), sorted. |  | 200 string[] |  |
| `POST /api/models/ids` | The ids of the models matching the query, in display order. | body: [ModelQuery](#modelquery) | 200 [IdList](#idlist) | 400 |
| `POST /api/models/rows` | Rows for `ids`, in that order; unknown ids are skipped. | body: [RowsRequest](#rowsrequest) | 200 [Rows](#rows) | 400 |
| `GET /api/models/{id}` | One model's display fields and its images. | path `id`: integer | 200 [ModelDetail](#modeldetail) | 404 |
| `GET /api/models/{id}/archive-files` | Every file inside the model's archive (name, size, CRC-32), as the last crawl or rescan read the archive's catalog; empty until then. | path `id`: integer | 200 [ArchiveFile](#archivefile)[] |  |
| `GET /api/models/{id}/files` | The printable files the crawl stored for a model, by name. | path `id`: integer | 200 [StlFile](#stlfile)[] |  |
| `POST /api/models/{id}/view` | Model files of the archive, loaded for the viewer in one pass over the archive: a bundle (`stl_core::view::encode_bundle`) of each file's scene (models, supports, build plates) in MeshPeek's wire format (`meshpeek_core::wire`), or why it has none. A file is at most 1 GB, the files together too; the server loads two requests at a time. | path `id`: integer<br>body: [ViewRequest](#viewrequest) | 200 `application/octet-stream` | 400, 404, 413, 422 |
| `GET /api/models/{id}/viewable` | The model files in the archive the viewer can show (STL, OBJ, PLY, 3MF, Lychee and CHITUBOX files), from the archive's catalog read now, by name. | path `id`: integer | 200 [ViewableFile](#viewablefile)[] | 404, 422 |
| `GET /api/models/{id}/warnings` | Which archive members trigger each of the model's stored warnings (the archive's catalog is read now). | path `id`: integer | 200 [WarningDetails](#warningdetails) | 404 |
| `POST /api/views/folder` | Thumbnail cells for the archives in a folder (and its subfolders), by path. | body: [FolderRequest](#folderrequest) | 200 [Cell](#cell)[] |  |
| `POST /api/views/grid` | Thumbnail cells for models, in the order asked. | body: [GridRequest](#gridrequest) | 200 [Cell](#cell)[] | 400 |

### Editing models

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/editor/choices` | Completion lists: `titles`, `creators`, `sources`, `dates`, `tags`, `releases`, `sculptors`, `collections`, `subcollections`, `categories`, `scales`. |  | 200 object |  |
| `GET /api/editor/subcollections` | The subcollection names of one collection. | query `collection`: string | 200 string[] |  |
| `DELETE /api/images/{id}` | Remove one image and its thumbnail. | path `id`: integer | 204 | 404 |
| `PATCH /api/models/cells` | Apply browser-table cell edits in one transaction, in order. Tags are lowercased and de-duplicated; a subcollection belongs to its collection. | body: [CellEdits](#celledits) | 200 [Changed](#changed) | 400, 403 |
| `POST /api/models/delete` | Remove models from the library (their images and thumbnails too). The archive files are not touched. | body: [IdsRequest](#idsrequest) | 200 [Changed](#changed) | 403 |
| `POST /api/models/mass-edit` | Apply the mass editor's changes to the listed models in one transaction. | body: [MassEdit](#massedit) | 200 [MassResult](#massresult) | 400 |
| `POST /api/models/parse-paths` | What a path pattern extracts from each model's archive path (the mass editor's "Show parsed values"); nothing is saved. | body: [ParseRequest](#parserequest) | 200 [Parsed](#parsed)[] |  |
| `GET /api/models/{id}/editor` | A model as the editor shows it. | path `id`: integer | 200 [EditorModel](#editormodel) | 404 |
| `PUT /api/models/{id}/editor` | Save the editor's form for one model. | path `id`: integer<br>body: [EditorSave](#editorsave) | 204 | 400, 404 |
| `POST /api/models/{id}/images` | Add images to a model (multipart form, one file per part). Each becomes a thumbnail (resized to the library's thumbnail size, or its view capture size with `view`); the original is not kept. | path `id`: integer<br>query `view`?: boolean<br>body: `multipart/form-data` | 200 [AddedImages](#addedimages) | 404, 413 |
| `POST /api/sort/preview` | Where the archives would go (nothing is moved). | body: [SortRequest](#sortrequest) | 200 [SortRow](#sortrow)[] | 403 |

### Lookups (sculptors, collections, tags…)

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/authors` | Every creator name with its model count. |  | 200 [Author](#author)[] |  |
| `POST /api/authors` | Rename, merge or delete a creator name across all models. | body: [AuthorChange](#authorchange) | 204 | 400 |
| `GET /api/authors/profile` | A creator's profile (404 when there is none). | query `name`: string | 200 [Profile](#profile) | 404 |
| `PUT /api/authors/profile` | Create or update a creator's profile; blank values are stored as null. | query `name`: string<br>body: [Profile](#profile) | 204 |  |
| `GET /api/lookups/{kind}` | Every row of a lookup table by name (subcollections by collection, then name). | path `kind`: string | 200 [LookupRow](#lookuprow)[] | 404 |
| `POST /api/lookups/{kind}` | Add a row. 409 when the name exists (within the collection, for a subcollection) or the parent collection does not. | path `kind`: string<br>body: [NewLookup](#newlookup) | 201 | 409 |
| `POST /api/lookups/{kind}/merge` | Merge two rows: models of `remove` get `keep`, then `remove` is deleted. | path `kind`: string<br>body: [Merge](#merge) | 204 | 404 |
| `GET /api/lookups/{kind}/{id}` | What deleting a row would affect. | path `kind`: string<br>path `id`: integer | 200 [DeleteInfo](#deleteinfo) | 404 |
| `PATCH /api/lookups/{kind}/{id}` | Rename a row; 409 when the new name is taken. | path `kind`: string<br>path `id`: integer<br>body: [Rename](#rename) | 204 | 404, 409 |
| `DELETE /api/lookups/{kind}/{id}` | Delete a row. Models lose the value; a collection takes its subcollections. | path `kind`: string<br>path `id`: integer | 204 | 404 |
| `POST /api/tags/merge` | Merge tags into one (Tag editor → Merge). | body: [TagMerge](#tagmerge) | 204 |  |
| `GET /api/tags/unused` | Tags no model uses. |  | 200 [Count](#count) |  |
| `DELETE /api/tags/unused` | Delete the tags no model uses. |  | 200 [Count](#count) |  |

### Thumbnails and images

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/images/{id}/original` | An image at full size: the original the thumbnail was made from, read from the archive (or from the file beside it). 404 when it cannot be had (an image added by hand, a moved archive): show the thumbnail instead. | path `id`: integer | 200 `image/jpeg` | 404 |
| `GET /api/images/{id}/thumbnail` | An image's thumbnail: a redirect to its content address. | path `id`: integer | 200 `image/jpeg` | 308, 404 |
| `GET /api/thumb/{digest}` | A thumbnail by content address. | path `digest`: string | 200 `image/jpeg` | 404 |

### Files and folders

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/files/browse` | List a folder on the server (editor role): subfolders and archives. | query `path`?: string | 200 [Listing](#listing) | 403, 404 |
| `GET /api/models/{id}/archive` | Download a model's archive file. | path `id`: integer | 200 `application/octet-stream` | 404 |

### Libraries and their folders

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/libraries` | Every library with its folders, by name. |  | 200 [LibraryInfo](#libraryinfo)[] |  |
| `POST /api/libraries` | Add a folder (editors, inside their roots) to a library, or as a new library: 201, or 200 with the folder already there (in that library). Records of folders around it below the folder move into it. | body: [NewLibrary](#newlibrary) | 200 [LibraryInfo](#libraryinfo)<br>201 [LibraryInfo](#libraryinfo) | 400, 403, 404 |
| `PATCH /api/libraries/folders/{id}` | Set a library folder's pattern, monitor it, or move it with its records to another library or a new one (editors). A library left without folders goes. | path `id`: integer<br>body: [FolderChange](#folderchange) | 200 [LibraryFolder](#libraryfolder) | 400, 404 |
| `POST /api/libraries/folders/{id}/remove` | Remove one folder of a library (editors): its records go to the folder around it, or are deleted with their images when there is none. A library left without folders goes too. Files are not touched. | path `id`: integer | 200 [LibraryRemoved](#libraryremoved) | 404 |
| `PATCH /api/libraries/{id}` | Rename a library (editors). A name another library has gets a number. | path `id`: integer<br>body: [LibraryChange](#librarychange) | 200 [LibraryInfo](#libraryinfo) | 400, 404 |
| `POST /api/libraries/{id}/remove` | Remove a library with all its folders (editors): their records go to a folder of another library around them, or are deleted with their images. Files are not touched. | path `id`: integer | 200 [LibraryRemoved](#libraryremoved) | 404 |

### Jobs and events

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/events` | The event stream: `ServerEvent` JSON text frames (job progress and logs, library changes). Authenticates like any route (the session cookie works for browsers, which cannot set headers on a WebSocket). |  | 101 [ServerEvent](#serverevent) |  |
| `GET /api/jobs` | Queued, running and recently finished jobs, oldest first. |  | 200 [JobInfo](#jobinfo)[] |  |
| `POST /api/jobs` | Queue a job. Jobs run one at a time, in order. Needs the editor role (compacting the database and backups: admin). | body: [JobRequest](#jobrequest) | 202 [JobInfo](#jobinfo) | 403 |
| `GET /api/jobs/{id}` | One job with its log. | path `id`: integer | 200 [JobDetail](#jobdetail) | 404 |
| `DELETE /api/jobs/{id}` | Cancel a job: a queued one at once, a running one at its next checkpoint (what it finished so far stays). 409 when it has already finished. | path `id`: integer | 202 | 404, 409 |

### Maintenance

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/maintenance/merge-moved` | Merge each missing record into the record found at its new location: the metadata of the missing one moves there (Merge Moved). | body: [MergeMoved](#mergemoved) | 200 [Merged](#merged) | 403 |
| `POST /api/maintenance/remove` | Remove records from the library and prune lookups left unused (Remove Selected / All Missing). The archive files are not touched. | body: [RemoveRecords](#removerecords) | 200 [Removed](#removed) | 403 |
| `POST /api/maintenance/remove-missing` | Remove the records of every missing archive (editors), as *Remove* does for a selection: archives gone from monitored folders are marked missing, and this clears them out. The archive files are not touched. |  | 200 [Removed](#removed) | 403 |

### Backups

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/backups` | The backups on the server, newest first. |  | 200 [BackupFile](#backupfile)[] | 403 |
| `GET /api/backups/{name}` | Download a backup. | path `name`: string | 200 `application/x-7z-compressed` | 404 |
| `DELETE /api/backups/{name}` | Delete a backup. | path `name`: string | 204 | 404 |

### Search

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/search/image` | Search with an image (multipart, one file). | query `method`?: string \| null<br>query `max_distance`?: integer \| null<br>query `threshold`?: number \| null<br>body: `multipart/form-data` | 200 [SearchResult](#searchresult) | 400, 413 |

### Statistics

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/stats/column` | How the models spread over one column's values, most frequent first. | query `key`: string<br>query `top`?: integer \| null | 200 [ColumnStats](#columnstats) | 400 |
| `GET /api/stats/library` | Counts, sums and rankings for the Statistics view. |  | 200 [LibraryStats](#librarystats) |  |

### Library settings

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/settings/library` | The library settings. |  | 200 [Settings](#settings) |  |
| `PUT /api/settings/library` | Change the library settings (administrators). Takes every field; the desktop app reads the same file. | body: [Settings](#settings) | 200 [Settings](#settings) | 400, 403 |

### AI tagger

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `POST /api/ai/commit/{id}` | Save a reviewed suggestion to a model. | path `id`: integer<br>body: [AiCommit](#aicommit) | 200 [AiSaved](#aisaved) | 404 |
| `GET /api/ai/status` | Whether the AI tagger can run, and with what. |  | 200 [AiStatus](#aistatus) |  |

### Compute workers

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/workers` | The compute mode and the connected workers. |  | 200 [WorkersStatus](#workersstatus) |  |
| `GET /api/workers/connect` | A compute worker's connection (editors): a WebSocket of JSON frames, the worker's `hello` first (see `crate::workers::ToServer` / `ToWorker`). |  | 101 | 403 |
| `POST /api/workers/token` | A session token for a compute worker (editors): the web client asks for one and hands it to the desktop app, which starts the worker with it. |  | 200 [WorkerToken](#workertoken) | 403 |

### Optional components

| Endpoint | Description | Request | Response | Errors |
|---|---|---|---|---|
| `GET /api/components` | The downloadable components and whether each is installed. |  | 200 [ComponentsInfo](#componentsinfo) | 403 |

## Types

The request and response bodies named above.

### AddedImages

| Field | Type | Description |
|---|---|---|
| `added` | [ImageRef](#imageref)[] |  |
| `failed` | string[] | File names that could not be read as images. |

### AiCommit

| Field | Type | Description |
|---|---|---|
| `fields` | [AiFieldValues](#aifieldvalues) |  |
| `overwrite` (optional) | boolean | Replace values already set (default: fill empty fields only). Tags are merged. |
| `tags` (optional) | string[] |  |

### AiFieldValues

The metadata fields the tagger fills (`normalize_meta`).

| Field | Type | Description |
|---|---|---|
| `collection` (optional) | string |  |
| `creator` (optional) | string |  |
| `description` (optional) | string |  |
| `scale` (optional) | string |  |
| `sculptor` (optional) | string |  |
| `source` (optional) | string | `patreon`, `kickstarter`, `myminifactory` or empty. |
| `subcollection` (optional) | string |  |
| `title` (optional) | string |  |
| `year` (optional) | integer \| null |  |

### AiSaved

| Field | Type | Description |
|---|---|---|
| `saved` | boolean |  |

### AiStatus

What the AI tagger can use.

| Field | Type | Description |
|---|---|---|
| `available` | boolean |  |
| `detail` | string | Why it is not available, or where it comes from. |
| `model` | string |  |
| `running` | boolean | A started `llama-server` is running (always false with `url`). |

### AiSuggestion

What the model suggests for one model.

| Field | Type | Description |
|---|---|---|
| `archive_path` | string |  |
| `fields` | [AiFieldValues](#aifieldvalues) |  |
| `image_ids` | integer[] | The images sent (their thumbnails), in order. |
| `model_id` | integer |  |
| `tags` | string[] |  |
| `title` | string |  |

### ArchiveFile

One file inside a model's archive.

| Field | Type | Description |
|---|---|---|
| `crc` | string | The member's CRC-32 from the archive's catalog, as 8 hex digits. |
| `name` | string | The member's name in the archive (`/` separators). |
| `size` (optional) | integer \| null | Uncompressed size, when the archive records it. |

### AuthStatus

| Field | Type | Description |
|---|---|---|
| `setup_required` | boolean | No user exists yet: the first administrator must be created (`POST /api/auth/setup`, from the server's own machine). |
| `user` (optional) | null \| [User](#user) |  |
| `version` | string |  |

### Author

| Field | Type | Description |
|---|---|---|
| `models` | integer |  |
| `name` | string |  |

### AuthorChange

| Field | Type | Description |
|---|---|---|
| `action` | string | `rename` (`name` → `to`), `merge` (`name` into `to`, keeping `to`'s profile) or `delete` (clear `name` from its models). |
| `name` | string |  |
| `to` (optional) | string \| null |  |

### BTreeMap

Type: map of map of integer

### BackupFile

| Field | Type | Description |
|---|---|---|
| `modified` | integer | Unix seconds. |
| `name` | string |  |
| `size` | integer |  |

### Cell

One model as a thumbnail cell.

| Field | Type | Description |
|---|---|---|
| `archive_path` (optional) | string \| null |  |
| `creator` (optional) | string \| null |  |
| `id` | integer |  |
| `images` | integer | How many images the model has. |
| `thumbnail` (optional) | string \| null | The first thumbnail. |
| `title` (optional) | string \| null |  |

### CellEdit

| Field | Type | Description |
|---|---|---|
| `field` | string | `title`, `creator`, `source`, `date`, `tags`, `sculptor`, `collection`, `subcollection`, `category`, `sequence`, `release`, `scale`, `presupports` or `to_print`. |
| `id` | integer |  |
| `value` (optional) | string \| null | The cell text as typed; empty or null clears the field. |

### CellEdits

| Field | Type | Description |
|---|---|---|
| `edits` | [CellEdit](#celledit)[] |  |

### Changed

| Field | Type | Description |
|---|---|---|
| `models` | integer | How many of the listed models exist (and were changed). |

### CollectionNode

| Field | Type | Description |
|---|---|---|
| `id` | integer |  |
| `name` | string |  |
| `subcollections` | [NamedId](#namedid)[] |  |

### ColumnRow

| Field | Type | Description |
|---|---|---|
| `count` | integer |  |
| `label` | string |  |

### ColumnStats

| Field | Type | Description |
|---|---|---|
| `rows` | [ColumnRow](#columnrow)[] |  |
| `total` | integer | Models in the library. |

### ComponentStatus

One component as the Maintenance page shows it.

| Field | Type | Description |
|---|---|---|
| `default_variant` (optional) | string \| null | The variant to offer, from the GPUs found. |
| `detail` | string | Where it is, or why it is in that state. |
| `enables` | string |  |
| `id` | string |  |
| `label` | string |  |
| `license` | string |  |
| `license_url` | string |  |
| `sizes` | [VariantSize](#variantsize)[] | Download sizes per variant (`default` for components without variants). |
| `state` | string | `installed`, `missing`, `external` (configured elsewhere, not managed here) or `unavailable` (no download for this platform). |
| `variant` (optional) | string \| null | The installed variant. |
| `variants` | string[] | Its variants here (`cpu`, `cuda`, `directml`); empty when it has none. |

### ComponentsInfo

| Field | Type | Description |
|---|---|---|
| `clip_provider` (optional) | string \| null | The execution provider of the loaded CLIP encoder (`CPU`, `CUDA`, `DirectML`, …); null until it is first used. |
| `components` | [ComponentStatus](#componentstatus)[] |  |
| `data_dir` | string | The data directory the components go into. |
| `free_bytes` (optional) | integer \| null | Free space where the data directory is. |
| `gpus` | string[] | GPU vendors found: `nvidia`, `amd`, `intel` (on Windows only `nvidia` is detected). |
| `platform` (optional) | string \| null | `windows-x64`, `linux-x64`, or null when nothing can be downloaded here. |

### ContentRow

Two archives sharing STL files (name and size).

| Field | Type | Description |
|---|---|---|
| `id_a` | integer |  |
| `id_b` | integer |  |
| `overlap` | integer | Files in both. |
| `path_a` (optional) | string \| null |  |
| `path_b` (optional) | string \| null |  |
| `similarity` | number | `overlap` over the larger list. |
| `title_a` (optional) | string \| null |  |
| `title_b` (optional) | string \| null |  |
| `total_a` | integer |  |
| `total_b` | integer |  |

### Count

| Field | Type | Description |
|---|---|---|
| `count` | integer |  |

### Credentials

| Field | Type | Description |
|---|---|---|
| `name` | string |  |
| `password` | string |  |

### DateDto

| Field | Type | Description |
|---|---|---|
| `month` (optional) | integer \| null | Absent (or null) for the whole year. |
| `year` | integer |  |

### DeleteInfo

| Field | Type | Description |
|---|---|---|
| `models` | integer | Models that lose the value. |
| `name` | string |  |
| `subcollections` | integer | Subcollections deleted with a collection. |

### EditorModel

| Field | Type | Description |
|---|---|---|
| `archive_path` (optional) | string \| null |  |
| `category` (optional) | string \| null |  |
| `collection` (optional) | string \| null |  |
| `creator` (optional) | string \| null |  |
| `date` | string | `YYYY-MM`, `YYYY` or empty. |
| `description` (optional) | string \| null |  |
| `id` | integer |  |
| `images` | [ImageRef](#imageref)[] |  |
| `presupports` (optional) | string \| null |  |
| `release` (optional) | string \| null |  |
| `scales` | string[] |  |
| `sculptor` (optional) | string \| null |  |
| `sequence` (optional) | integer \| null |  |
| `source` (optional) | string \| null |  |
| `subcollection` (optional) | string \| null |  |
| `tags` | string[] |  |
| `title` (optional) | string \| null |  |
| `to_print` | boolean |  |
| `url` (optional) | string \| null |  |

### EditorSave

The editor's form. Empty strings clear; lookups are created by name.

| Field | Type | Description |
|---|---|---|
| `category` (optional) | string |  |
| `collection` (optional) | string |  |
| `creator` (optional) | string |  |
| `date` (optional) | string | `YYYY-MM`, `YYYY_MM`, `YYYY` or empty. |
| `description` (optional) | string |  |
| `presupports` (optional) | string |  |
| `release` (optional) | string |  |
| `scales` (optional) | string[] |  |
| `sculptor` (optional) | string |  |
| `sequence` (optional) | integer | 0 clears. |
| `source` (optional) | string |  |
| `subcollection` (optional) | string |  |
| `tags` (optional) | string[] |  |
| `title` (optional) | string |  |
| `url` (optional) | string |  |

### Entry

| Field | Type | Description |
|---|---|---|
| `kind` | string | `folder` or `archive`. |
| `name` | string |  |
| `path` | string |  |
| `size` (optional) | integer \| null | Archives only. |

### ErrorBody

The body of every error response.

| Field | Type | Description |
|---|---|---|
| `error` | string |  |

### FilterSpecDto

Include/exclude values per facet (`creator`, `tag`, `path`, `date`, `sculptor`, …), has-value / no-value groups and an optional id list. Lookup facets take ids, `date` takes `{year, month}`, the others text.

| Field | Type | Description |
|---|---|---|
| `exclude` (optional) | map of [FilterValueDto](#filtervaluedto)[] |  |
| `has_value` (optional) | string[] |  |
| `ids` (optional) | integer[] \| null |  |
| `include` (optional) | map of [FilterValueDto](#filtervaluedto)[] |  |
| `no_value` (optional) | string[] |  |

### FilterTree

| Field | Type | Description |
|---|---|---|
| `categories` | [NamedId](#namedid)[] |  |
| `collections` | [NamedId](#namedid)[] |  |
| `counts` | [BTreeMap](#btreemap) |  |
| `creators` | string[] |  |
| `dates` | [DateDto](#datedto)[] |  |
| `filetypes` | string[] |  |
| `folders` | string[] |  |
| `libraries` | [NamedId](#namedid)[] |  |
| `new_folders` (optional) | string[] \| null |  |
| `path_counts` (optional) | map of integer \| null | Models per folder, ancestors included. |
| `presupports` | string[] |  |
| `releases` | [NamedId](#namedid)[] |  |
| `scales` | [NamedId](#namedid)[] |  |
| `scoped` | boolean | Whether the counts are scoped by a filter. |
| `scoped_counts` | [BTreeMap](#btreemap) |  |
| `scoped_folders` (optional) | string[] \| null |  |
| `scoped_path_counts` (optional) | map of integer \| null |  |
| `sculptors` | [NamedId](#namedid)[] |  |
| `sources` | string[] |  |
| `subcollections` | [CollectionNode](#collectionnode)[] |  |
| `tags` | string[] |  |

### FilterTreeRequest

| Field | Type | Description |
|---|---|---|
| `counts` (optional) | boolean | Compute per-value counts. |
| `filter` (optional) | null \| [FilterSpecDto](#filterspecdto) |  |
| `show` (optional) | string[] | Sections to fill: `author`, `tags`, `source`, `date`, `sculptor`, `collection`, `subcollection`, `category`, `release`, `scale`, `presupports`, `path`. |

### FilterValueDto

A selected filter value: text, a lookup id, or a release date.

One of:

- variant
- variant
- [DateDto](#datedto)

### FindReplace

| Field | Type | Description |
|---|---|---|
| `case` (optional) | boolean | Case-sensitive matching. |
| `fields` | string[] | Any of `title`, `creator`, `source`, `url`. |
| `find` | string |  |
| `regex` (optional) | boolean | `find` is a regular expression (Rust fancy-regex); `replace` then takes `\1`, `\g<name>`, `\n` as in Python. |
| `replace` | string |  |

### FolderChange

| Field | Type | Description |
|---|---|---|
| `library` (optional) | integer \| null | Move the folder (with its records) to this library. |
| `monitored` (optional) | boolean \| null |  |
| `new_library` (optional) | string \| null | Move the folder (with its records) to a new library with this name. |
| `pattern` (optional) | string \| null | A path pattern; `""` clears it. |

### FolderRequest

| Field | Type | Description |
|---|---|---|
| `folder` | string | A folder from `/api/folders`. |
| `include_subfolders` (optional) | boolean |  |

### GridRequest

| Field | Type | Description |
|---|---|---|
| `ids` | integer[] | The models, in the order wanted (at most 500). |

### IdList

| Field | Type | Description |
|---|---|---|
| `ids` | integer[] |  |

### IdsRequest

| Field | Type | Description |
|---|---|---|
| `ids` | integer[] |  |

### ImagePair

Two archives with matching images.

| Field | Type | Description |
|---|---|---|
| `best` | number | The best match: differing bits (`dhash`) or cosine similarity (`clip`). |
| `id_a` | integer |  |
| `id_b` | integer |  |
| `imgs_a` | integer | Images of the model compared. |
| `imgs_b` | integer |  |
| `pairs` | integer | Matching image pairs. |
| `path_a` | string |  |
| `path_b` | string |  |
| `size_a` (optional) | integer \| null |  |
| `size_b` (optional) | integer \| null |  |
| `title_a` | string |  |
| `title_b` | string |  |

### ImageRef

| Field | Type | Description |
|---|---|---|
| `id` | integer |  |
| `thumbnail` (optional) | string \| null | Where the thumbnail is served; absent when the image has none. |

### ImageResult

| Field | Type | Description |
|---|---|---|
| `images` | integer | Images compared. |
| `items` | integer | Distinct images (identical ones collapsed). |
| `method` | string | `dhash` or `clip`. |
| `near_pairs` | integer |  |
| `pairs` | [ImagePair](#imagepair)[] |  |
| `shared_skipped` | integer | Distinct images left out as shared by too many models. |
| `truncated` | boolean | More pairs matched than are kept. |

### JobDetail

| Field | Type | Description |
|---|---|---|
| `job` | [JobInfo](#jobinfo) |  |
| `log` | string[] | The job's log, oldest line first (at most the last 2,000 lines). |
| `result` (optional) | null \| [JobResult](#jobresult) |  |

### JobInfo

| Field | Type | Description |
|---|---|---|
| `created_at` | integer |  |
| `done` | integer |  |
| `finished_at` (optional) | integer \| null |  |
| `id` | integer |  |
| `kind` | string |  |
| `message` (optional) | string \| null | The outcome (a summary, or the error). |
| `phase` (optional) | string \| null |  |
| `request` | [JobRequest](#jobrequest) |  |
| `started_at` (optional) | integer \| null |  |
| `state` | [JobState](#jobstate) |  |
| `total` | integer |  |
| `user` | string | Who submitted it. |

### JobRequest

What to run. Crawl settings not given here come from the library's `settings.json`, as in the desktop app.

One of:

- `kind: "crawl"` — `folder`, `force_rescan`, `keep_missing`, `pattern`, `recurse`: Crawl a folder of archives.
- `kind: "update_folders"` — `folders`: Re-crawl crawled folders (all of them, or `folders`) with their stored patterns, matching archives moved from one folder to another before deleting the records of archives that are gone (Update Folders).
- `kind: "clear_library"` — `confirm`: Delete every record, lookup and thumbnail of the library (Delete Database; administrators). `confirm` must be `"DELETE"`.
- `kind: "verify_archives"`: Check that every archive is still on disk, unchanged in size.
- `kind: "recompute_dhashes"` — `all`: Compute perceptual hashes from stored thumbnails (all, or only missing / outdated).
- `kind: "prune_images"` — `keep`: Keep at most `keep` images per model (the oldest).
- `kind: "compact_database"`: `VACUUM` the database and truncate its write-ahead log; drop unused thumbnails and rewrite the thumbnail store without its free pages.
- `kind: "compute_embeddings"` — `all`: Compute CLIP embeddings from stored thumbnails (all, or only images without one).
- `kind: "find_content_duplicates"` — `threshold`: Find Duplicate Archives → STL files: archive pairs whose file lists (name and size) overlap by at least `threshold` (0–1) of the larger list.
- `kind: "find_image_duplicates"` — `max_distance`, `max_models`, `method`, `threshold`: Find Duplicate Archives → Images: archive pairs with matching images.
- `kind: "generate_sidecars"`: Write `METADATA.7z` in every folder holding catalogued archives (needs sidecars enabled in the library settings).
- `kind: "remove_sidecars"`: Delete `METADATA.7z` from every folder holding catalogued archives.
- `kind: "clean_sidecars"`: Tidy the `METADATA.7z` files under the crawled folders: rewrite those listing archives that are gone, delete those whose archives are all gone, and delete leftover `METADATA.7z.tmp` files.
- `kind: "backup"`: Back up the database, thumbnails and settings to `<data dir>/backups/stlLibrary_backup_<stamp>.7z` (administrators).
- `kind: "install_components"` — `components`, `variants`: Download optional components into the data directory (administrators): `ai_runtime`, `ai_model`, `clip_model`, `onnxruntime`, `pdfium`. `variants` picks a variant per component (`ai_runtime`: `cpu`, or `cuda` on Windows; `onnxruntime`: `cpu`, `directml` on Windows, `cuda` on Linux); the others get the default for the GPUs found.
- `kind: "remove_components"` — `components`: Delete downloaded components (administrators).
- `kind: "rescan"` — `ids`: Re-read the models' archives: metadata, presupports, warnings, scales, hash (Rescan).
- `kind: "generate_thumbnails"` — `ids`, `max_thumbnails`: Replace the models' images with ones from their archives, at most `max_thumbnails` each (default: the library setting).
- `kind: "sort_files"` — `ids`, `pattern`, `target_dir`: Move the models' archives into a folder tree under `target_dir` built from `pattern` (Sort Files). Needs *file sorting* in the settings.
- `kind: "relocate_library"` — `apply`, `fix_case`, `id`, `root`: Move a library's folder to a new root: check every archive there and, with `apply`, set the root (and those of the folders inside it). With `fix_case`, paths found only with another letter case take the spelling on disk. Nothing is re-read.
- `kind: "delete_files"` — `ids`: Move the models' archive files to the trash folder (or delete them, without one) and remove the records. Needs *allow file delete*.
- `kind: "ai_suggest"` — `id`, `ignore_meta`, `max_tags`: Ask the AI tagger for one model's tags and metadata; nothing is saved (save the reviewed result with `/api/ai/commit/{id}`).
- `kind: "ai_tag"` — `ids`, `ignore_meta`, `max_tags`, `overwrite`: Tag every model with the AI tagger and save the results: empty fields only, unless `overwrite`; tags are merged.
- `kind: "find_duplicate_entries"`: Records cataloguing the same archive more than once (same content hash, else same file name and size).
- `kind: "find_moved"` — `ids`: For each record, another record with the same content whose archive is on disk (Find Moved in DB); merge them with `/api/maintenance/merge-moved`.
- `kind: "search_folder"` — `folder`, `ids`: Look under `folder` for the records' archives (by content hash) and point the records at the archives found.
- `kind: "recheck_warnings"` — `warning`: Re-read every archive's file list and fix one warning line: `no_images` or `presupported`.
- `kind: "find_metadata_duplicates"` — `columns`, `max_distance`, `method`: Find Duplicates (metadata): models with equal or similar values.

### JobResult

What a finder or maintenance job found (`JobDetail.result`).

One of:

- `type: "content"` — `pairs`
- variant
- `type: "metadata"` — `groups`
- `type: "records"` — `rows`: Records of a maintenance list (Verify, duplicate entries, moved, found).
- variant: The AI tagger's suggestion for one model (`ai_suggest`).
- variant: What moving a library to a new root found (`relocate_library`).

### JobState

Type: `"queued"` \| `"running"` \| `"done"` \| `"failed"` \| `"cancelled"`

### LibraryChange

| Field | Type | Description |
|---|---|---|
| `name` | string |  |

### LibraryFolder

One folder of a library.

| Field | Type | Description |
|---|---|---|
| `archive_count` (optional) | integer \| null | Archives found by its last crawl. |
| `id` | integer |  |
| `last_crawled_at` (optional) | string \| null |  |
| `models` | integer | Records in it. |
| `monitored` | boolean |  |
| `name` | string | Unique among folders (`stl-server libraries relocate` takes it). |
| `origin` (optional) | string \| null | `migration` when the move to libraries made it for archives outside every crawled folder. |
| `pattern` (optional) | string \| null | Path pattern its crawls use. |
| `reachable` | boolean | Its root is a folder on the server (else it must be relocated). |
| `root` | string | Where the folder is on the server. |
| `uuid` | string | The same on every instance (export, sync). |

### LibraryInfo

| Field | Type | Description |
|---|---|---|
| `folders` | [LibraryFolder](#libraryfolder)[] | By root. |
| `id` | integer |  |
| `models` | integer | Records in its folders. |
| `name` | string |  |
| `uuid` | string | The same on every instance (export, sync). |

### LibraryRemoved

| Field | Type | Description |
|---|---|---|
| `deleted` | integer | Records deleted (no folder around it). The archive files stay. |
| `moved` | integer |  |
| `moved_to` (optional) | integer \| null | The folder (of another library) around it that took its records. |

### LibraryStats

| Field | Type | Description |
|---|---|---|
| `rankings` | map of [RankEntry](#rankentry)[] | `by_source`, `by_year`, `top_sculptors`, … each most frequent first. |
| `scalars` | map of number | `total_models`, `total_images`, `total_archive_size`, `presupported`, … |

### Listing

| Field | Type | Description |
|---|---|---|
| `entries` | [Entry](#entry)[] | Folders first, then archives, each by name. |
| `parent` (optional) | string \| null | Its parent, when the user may go there. |
| `path` | string | The listed folder (empty for the starting points). |
| `truncated` | boolean | More entries exist than were returned. |

### LookupRow

| Field | Type | Description |
|---|---|---|
| `collection` (optional) | string \| null |  |
| `collection_id` (optional) | integer \| null | Subcollections only. |
| `id` | integer |  |
| `models` | integer | Models using it. |
| `name` | string |  |

### MassEdit

| Field | Type | Description |
|---|---|---|
| `category` (optional) | string |  |
| `clearing` (optional) | string[] | Fields to clear. |
| `collection` (optional) | string |  |
| `find` (optional) | null \| [FindReplace](#findreplace) |  |
| `ids` (optional) | integer[] |  |
| `manual` (optional) | map of string \| null | `source`, `creator`, `release_date`, `url`, `title` → template (`{token}`s from the path pattern and file name), or null to clear. |
| `overwrite` (optional) | boolean | Path values replace existing ones (otherwise they fill empty fields). |
| `pattern` (optional) | string | A path pattern (`{sculptor}/{release}/{title}`) parsed from each model's archive path; its values are applied per model. |
| `presupports` (optional) | string \| null | Null: no change; empty: clear. |
| `release` (optional) | string |  |
| `scale` (optional) | string |  |
| `sculptor` (optional) | string |  |
| `subcollection` (optional) | string |  |
| `tags` (optional) | string | Comma-separated tags to add (a template). |
| `trim` (optional) | boolean | Strip leading and trailing whitespace from the text fields. |

### MassResult

| Field | Type | Description |
|---|---|---|
| `models` | integer |  |
| `pattern_matches` | integer | Models whose archive path matched `pattern`. |

### Match

| Field | Type | Description |
|---|---|---|
| `archive` | string |  |
| `model_id` | integer |  |
| `score` | number | Bits differing (dHash) or cosine similarity (CLIP). |
| `thumbnail` (optional) | string \| null |  |
| `title` | string |  |

### Merge

| Field | Type | Description |
|---|---|---|
| `keep` | integer | The row that stays. |
| `remove` | integer | The row merged into it and deleted. |

### MergeMoved

| Field | Type | Description |
|---|---|---|
| `items` | [MovedRecord](#movedrecord)[] |  |

### Merged

| Field | Type | Description |
|---|---|---|
| `errors` | integer | Items whose records no longer exist. |
| `merged` | integer |  |

### MetadataGroup

Models with equal or similar values in one column.

| Field | Type | Description |
|---|---|---|
| `column` | string | The column's label. |
| `models` | [MetadataModel](#metadatamodel)[] |  |
| `similarity` | string | `Exact`, `Levenshtein: n` or `Soundex: code`. |

### MetadataModel

| Field | Type | Description |
|---|---|---|
| `id` | integer |  |
| `title` | string |  |
| `value` | string |  |

### ModelDetail

| Field | Type | Description |
|---|---|---|
| `client_path` (optional) | string \| null | The archive's path as this client sees it, when it can open it there (a client on the server's machine, or a `path_map` entry); otherwise offer `/api/models/{id}/archive`. |
| `fields` | object | `title`, `creator`, `source`, `sculptor_name`, `collection_name`, `subcollection_name`, `category_name`, `release_name`, `release_year`, `release_month`, `archive_path`, `archive_size`, `is_missing`, `tags` (comma-separated) and `image_count`. |
| `id` | integer |  |
| `images` | [ImageRef](#imageref)[] |  |

### ModelQuery

| Field | Type | Description |
|---|---|---|
| `filter` (optional) | [FilterSpecDto](#filterspecdto) |  |
| `search` (optional) | string | Matched against title, creator, path and tags (case-insensitive). |
| `sort` (optional) | null \| [Sort](#sort) |  |

### MovedRecord

| Field | Type | Description |
|---|---|---|
| `id` | integer | The missing record, whose metadata is kept. |
| `path` | string | The archive path of the record crawled at the new location. |

### NamedId

| Field | Type | Description |
|---|---|---|
| `id` | integer |  |
| `name` | string |  |

### NewLibrary

| Field | Type | Description |
|---|---|---|
| `library` (optional) | integer \| null | The library to add the folder to; without it, a new library. |
| `name` (optional) | string \| null | A new library's name (default: the folder's name). |
| `path` | string | The folder on the server. |
| `pattern` (optional) | string \| null | The folder's path pattern. |

### NewLookup

| Field | Type | Description |
|---|---|---|
| `collection` (optional) | string \| null | The parent collection's name (subcollections only). |
| `name` | string |  |

### NewUser

| Field | Type | Description |
|---|---|---|
| `name` | string |  |
| `password` | string |  |
| `role` | [Role](#role) |  |

### ParseRequest

| Field | Type | Description |
|---|---|---|
| `ids` | integer[] |  |
| `pattern` | string |  |

### Parsed

| Field | Type | Description |
|---|---|---|
| `archive_path` | string |  |
| `fields` | [][] | What the pattern extracted (`title`, `sculptor_name`, `release_name`, …), in pattern order; empty when the path does not match. |
| `id` | integer |  |

### PasswordChange

| Field | Type | Description |
|---|---|---|
| `current` | string |  |
| `new` | string |  |

### Profile

A creator's profile links; each may be null.

| Field | Type | Description |
|---|---|---|
| `artstation_url` (optional) | string \| null |  |
| `full_name` (optional) | string \| null |  |
| `gumroad_url` (optional) | string \| null |  |
| `myminifactory_url` (optional) | string \| null |  |
| `patreon_url` (optional) | string \| null |  |

### RankEntry

| Field | Type | Description |
|---|---|---|
| `count` | integer |  |
| `value` | object | The value (a name, a year, …); null for "none". |

### RecordRow

One record of a maintenance list (`_result_row`).

| Field | Type | Description |
|---|---|---|
| `hash` | string |  |
| `id` | integer |  |
| `match_path` | string | Another record's path (duplicates, moved) or where the archive was found. |
| `path` | string |  |
| `size` (optional) | integer \| null |  |
| `status` | string | `Missing`, `Flagged Missing`, `Modified`, `Duplicate`, `Moved (in DB)` or `Found`. |
| `title` | string |  |

### Relocation

The check (and move) of a library to a new root.

| Field | Type | Description |
|---|---|---|
| `applied` | boolean | The root was changed (else only checked). |
| `case_only` | integer | Found only with another letter case. |
| `found` | integer |  |
| `inner` | integer | Libraries inside it, moved along. |
| `library` | integer |  |
| `missing` | integer |  |
| `missing_examples` | string[] | Some relative paths not found. |
| `root` | string |  |
| `total` | integer |  |

### RemoveRecords

| Field | Type | Description |
|---|---|---|
| `ids` | integer[] |  |

### Removed

| Field | Type | Description |
|---|---|---|
| `pruned` | map of integer | Lookup rows no model uses any more, deleted with them: `{table: rows}`. |
| `removed` | integer |  |

### Rename

| Field | Type | Description |
|---|---|---|
| `name` | string |  |

### Role

What a user may do. Ordered: each role includes the ones before it.

Type: `"viewer"` \| `"editor"` \| `"admin"`

### Row

One table row.

| Field | Type | Description |
|---|---|---|
| `archive_folder` (optional) | string \| null | The archive's folder (forward slashes). |
| `archive_hash` (optional) | string \| null |  |
| `archive_path` (optional) | string \| null |  |
| `archive_size` (optional) | integer \| null |  |
| `category` (optional) | string \| null |  |
| `collection` (optional) | string \| null |  |
| `compression_rate` (optional) | number \| null |  |
| `created_at` (optional) | string \| null |  |
| `creator` (optional) | string \| null |  |
| `description` (optional) | string \| null |  |
| `id` | integer |  |
| `images` | integer | Images catalogued for the model. |
| `is_missing` | boolean | The archive was not on disk when last checked. |
| `library` | string |  |
| `library_id` | integer |  |
| `presupports` (optional) | string \| null |  |
| `rel_path` | string | The archive's path relative to its library (`/` form). |
| `release` (optional) | string \| null |  |
| `release_month` (optional) | integer \| null |  |
| `release_year` (optional) | integer \| null |  |
| `scales` | string | Comma-separated, by name. |
| `sculptor` (optional) | string \| null |  |
| `sequence` (optional) | integer \| null |  |
| `source` (optional) | string \| null |  |
| `subcollection` (optional) | string \| null |  |
| `tags` | string | Comma-separated, by name. |
| `title` (optional) | string \| null |  |
| `to_print` | boolean |  |
| `updated_at` (optional) | string \| null |  |
| `url` (optional) | string \| null |  |
| `warning` (optional) | string \| null |  |

### Rows

| Field | Type | Description |
|---|---|---|
| `rows` | [Row](#row)[] |  |

### RowsRequest

| Field | Type | Description |
|---|---|---|
| `ids` | integer[] |  |

### SearchResult

| Field | Type | Description |
|---|---|---|
| `capped` | boolean | All of the closest images matched: more may exist (tighten the threshold). |
| `matches` | [Match](#match)[] | Best first. |
| `method` | string |  |
| `outdated` | integer | dHash: stored hashes in an older format, not compared (run Recompute image hashes). |

### ServerEvent

What WebSocket clients receive (`/api/events`), as JSON text frames.

One of:

- `type: "job"` — `job`: A job was queued, started, progressed or finished.
- `type: "job_log"` — `id`, `line`: One log line of a running job.
- `type: "library_changed"` — `what`: The library changed (`models`, `lookups`, `crawl`, …): reload what is shown.
- `type: "resync"`: Events were dropped for this client (it was too slow); reload everything.

### ServerInfo

| Field | Type | Description |
|---|---|---|
| `models` | integer | Models in the library. |
| `name` | string | Always `"stlLibrary"`. |
| `version` | string |  |

### Session

| Field | Type | Description |
|---|---|---|
| `token` | string | For `Authorization: Bearer`; browsers can rely on the cookie instead. |
| `user` | [User](#user) |  |

### Settings

The settings, with their effective values (defaults filled in).

| Field | Type | Description |
|---|---|---|
| `ai_embeddings_during_crawl` | boolean |  |
| `ai_embeddings_enabled` | boolean |  |
| `ai_temperature` | number | The AI tagger's sampling temperature (0 to 2; lower is more literal). |
| `allow_file_delete` | boolean | *Delete files* may remove archives from disk. |
| `detect_changes_by_size` | boolean | Crawls and monitored folders take an archive of the stored size as unchanged, whatever its modification time. |
| `file_sorting_enabled` | boolean | *Sort files* may move archives. |
| `low_compression_threshold` | number | Compression rate above which an archive counts as poorly compressed. |
| `max_thumb_size` | integer | Longest thumbnail edge in pixels. |
| `max_thumbnails` | integer | Thumbnails taken per archive. |
| `max_workers` | integer | Parallel crawl workers. |
| `metadata_sidecar_enabled` | boolean | Write `METADATA.7z` sidecars into archive folders. |
| `metadata_thumbnails` | boolean | Include thumbnails in sidecars. |
| `monitor_interval_minutes` | integer | Monitored folders are checked this often (minutes, 1 to 1440). |
| `presupport_patterns` | string[] | Regular expressions for presupported STL file names (the portable subset). |
| `recurse_subdirs` | boolean |  |
| `trash_folder_path` | string |  |
| `use_trash_folder` | boolean | Deleted archives go to `trash_folder_path` (when set) instead of being deleted. |
| `view_thumb_size` | integer | Longest edge of a thumbnail made from a model viewer capture (default: `max_thumb_size`). |
| `warn_low_compression` | boolean |  |
| `warn_nested_archive` | boolean |  |
| `warn_no_images` | boolean |  |
| `warn_no_printable` | boolean |  |
| `warn_presupported_stl` | boolean |  |

### Sort

| Field | Type | Description |
|---|---|---|
| `column` | string | One of `title`, `creator`, `source`, `sculptor_name`, `collection_name`, `subcollection_name`, `sequence`, `release_name`, `archive_path`, `presupports`, `created_at`, `archive_size`, `compression_rate`, `category_name`. |
| `descending` (optional) | boolean |  |

### SortRequest

| Field | Type | Description |
|---|---|---|
| `ids` | integer[] |  |
| `pattern` | string | `{collection}/{creator}/{title}`: folders, then the file name. |
| `target_dir` | string | The folder the tree is built in (a server folder). |

### SortRow

| Field | Type | Description |
|---|---|---|
| `from` | string |  |
| `id` | integer |  |
| `status` | string | `OK`, `COLLISION` (a free name is used), `MISS`, `SKIP` or `ERROR`. |
| `to` | string |  |

### StlFile

| Field | Type | Description |
|---|---|---|
| `filename` | string |  |
| `hash` (optional) | string \| null | The file's content hash, when the crawl stored one. |
| `size` (optional) | integer \| null |  |

### TagMerge

| Field | Type | Description |
|---|---|---|
| `ids` | integer[] |  |
| `target` | string | The resulting tag's name (an existing tag, matched case-insensitively, or a new one). |

### User

| Field | Type | Description |
|---|---|---|
| `disabled` | boolean |  |
| `id` | integer |  |
| `name` | string |  |
| `role` | [Role](#role) |  |

### UserChange

| Field | Type | Description |
|---|---|---|
| `disabled` (optional) | boolean \| null |  |
| `password` (optional) | string \| null | A new password; ends the user's sessions. |
| `role` (optional) | null \| [Role](#role) |  |

### VariantSize

| Field | Type | Description |
|---|---|---|
| `bytes` | integer |  |
| `variant` | string |  |

### ViewRequest

| Field | Type | Description |
|---|---|---|
| `names` | string[] | The model files' names in the archive, as `viewable` lists them: 1 to 16 different names. |

### ViewableFile

A model file inside an archive that the viewer can show.

| Field | Type | Description |
|---|---|---|
| `name` | string | The member's name in the archive (`/` separators). |
| `size` (optional) | integer \| null | Uncompressed size, when the archive records it. |

### WarningDetails

| Field | Type | Description |
|---|---|---|
| `error` (optional) | string \| null | Why the archive's files could not be listed. |
| `sections` | [WarningSection](#warningsection)[] |  |

### WarningMember

| Field | Type | Description |
|---|---|---|
| `name` | string |  |
| `size` (optional) | integer \| null |  |

### WarningSection

| Field | Type | Description |
|---|---|---|
| `members` | [WarningMember](#warningmember)[] | The members that trigger it now (empty for warnings raised by an absence, or ones that no longer reproduce). |
| `warning` | string |  |

### WorkerInfo

A connected worker as users see it.

| Field | Type | Description |
|---|---|---|
| `busy` | integer | Requests it is working on. |
| `chat_model` (optional) | string \| null | The AI tagger's model, when it has one. |
| `clip_provider` (optional) | string \| null | Where its CLIP encoder runs, when this server uses it. |
| `connected_at` | integer | Unix seconds. |
| `done` | integer | Requests it has answered. |
| `id` | integer |  |
| `name` | string | The worker's machine. |
| `note` (optional) | string \| null | Why part of it is not used (a different CLIP model). |
| `user` | string | The user whose session it uses. |
| `version` | string |  |

### WorkerToken

| Field | Type | Description |
|---|---|---|
| `token` | string | A new session of the signed-in user, for `stl-server worker` (`STL_WORKER_TOKEN`). Signing out elsewhere does not end it. |

### WorkersStatus

| Field | Type | Description |
|---|---|---|
| `compute` | string | `local` or `workers` (see `server.json` → `compute`). |
| `workers` | [WorkerInfo](#workerinfo)[] |  |

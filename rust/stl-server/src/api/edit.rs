//! Editing (editor role): browser cell edits, the model editor, the mass
//! editor, removing models from the library, and images. Each change runs in
//! one transaction and then queues the touched folders' sidecars.

use std::collections::{BTreeMap, BTreeSet, HashMap};

use axum::Json;
use axum::extract::{Multipart, Path, Query, State};
use axum::http::StatusCode;
use serde::{Deserialize, Serialize};
use stl_core::storage::edit::{self as core_edit, EditError};
use stl_core::storage::model_edit::{self, EditorForm, Find, MassPlan};
use stl_core::storage::{browser, maintenance};
use utoipa::ToSchema;

use super::models::ImageRef;
use super::thumbs::thumbnail_url;
use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::library_settings::LibrarySettings;
use crate::{ApiError, AppState, Library};

impl From<EditError> for ApiError {
    fn from(e: EditError) -> Self {
        match e {
            EditError::Db(e) => e.into(),
            EditError::UnknownField(f) => ApiError::bad_request(format!("unknown field {f:?}")),
            EditError::Invalid(m) => ApiError::bad_request(m),
            EditError::Unsupported(m) => ApiError::bad_request(format!("unsupported input: {m}")),
        }
    }
}

fn editor(me: &CurrentUser) -> Result<(), ApiError> {
    me.require(Role::Editor)
}

// ── cell edits ───────────────────────────────────────────────────────────────

#[derive(Deserialize, Serialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct CellEdit {
    pub id: i64,
    /// `title`, `creator`, `source`, `date`, `tags`, `sculptor`, `collection`,
    /// `subcollection`, `category`, `sequence`, `release`, `scale`,
    /// `presupports` or `to_print`.
    pub field: String,
    /// The cell text as typed; empty or null clears the field.
    pub value: Option<String>,
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct CellEdits {
    pub edits: Vec<CellEdit>,
}

#[derive(Serialize, ToSchema)]
pub struct Changed {
    /// How many of the listed models exist (and were changed).
    pub models: usize,
}

/// Apply browser-table cell edits in one transaction, in order. Tags are
/// lowercased and de-duplicated; a subcollection belongs to its collection.
#[utoipa::path(patch, path = "/api/models/cells", tag = "editing", request_body = CellEdits,
    responses((status = 200, body = Changed), (status = 400, body = crate::error::ErrorBody),
              (status = 403, body = crate::error::ErrorBody)))]
pub async fn cells(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<CellEdits>) -> Result<Json<Changed>, ApiError> {
    editor(&me)?;
    for e in &req.edits {
        if !core_edit::CELL_FIELDS.contains(&e.field.as_str()) {
            return Err(ApiError::bad_request(format!("unknown field {:?}", e.field)));
        }
    }
    let edits: Vec<(i64, String, Option<String>)> = req.edits.into_iter().map(|e| (e.id, e.field, e.value)).collect();
    let paths = lib.run(move |lib| Ok(lib.db.with_tx(|tx| core_edit::apply_cell_edits(tx, &edits))?)).await?;
    // One path per applied edit: count each model once.
    let paths: BTreeSet<String> = paths.into_iter().collect();
    let models = paths.len();
    lib.schedule_sidecars(paths);
    Ok(Json(Changed { models }))
}

// ── model editor ─────────────────────────────────────────────────────────────

#[derive(Serialize, ToSchema)]
pub struct EditorModel {
    pub id: i64,
    pub archive_path: Option<String>,
    pub title: Option<String>,
    pub creator: Option<String>,
    pub source: Option<String>,
    /// `YYYY-MM`, `YYYY` or empty.
    pub date: String,
    pub sequence: Option<i64>,
    pub presupports: Option<String>,
    pub url: Option<String>,
    pub description: Option<String>,
    pub to_print: bool,
    pub release: Option<String>,
    pub sculptor: Option<String>,
    pub collection: Option<String>,
    pub subcollection: Option<String>,
    pub category: Option<String>,
    pub scales: Vec<String>,
    pub tags: Vec<String>,
    pub images: Vec<ImageRef>,
}

fn date_text(year: Option<i64>, month: Option<i64>) -> String {
    stl_core::path_pattern::format_release_date(year, month)
}

/// A model as the editor shows it.
#[utoipa::path(get, path = "/api/models/{id}/editor", tag = "editing",
    params(("id" = i64, Path, description = "Model id")),
    responses((status = 200, body = EditorModel), (status = 404, body = crate::error::ErrorBody)))]
pub async fn get_editor(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<i64>) -> Result<Json<EditorModel>, ApiError> {
    let m = lib
        .run(move |lib| {
            let Some(m) = lib.db.with(|c| model_edit::model_for_editor(c, id))? else { return Ok(None) };
            let ids: Vec<u64> = m.images.iter().map(|&(i, _)| i as u64).collect();
            let digests: HashMap<u64, Option<Vec<u8>>> = lib.thumbs.digests(&ids)?.into_iter().collect();
            let images = m
                .images
                .iter()
                .map(|&(image_id, _)| ImageRef {
                    id: image_id,
                    thumbnail: digests.get(&(image_id as u64)).map(|d| thumbnail_url(image_id, d.as_deref())),
                })
                .collect();
            let r = m.row;
            Ok(Some(EditorModel {
                id,
                archive_path: m.archive_path,
                date: date_text(r.release_year, r.release_month),
                title: r.title,
                creator: r.creator,
                source: r.source,
                sequence: r.sequence,
                presupports: r.presupports,
                url: r.url,
                description: r.description,
                to_print: r.to_print,
                release: m.release,
                sculptor: m.sculptor,
                collection: m.collection,
                subcollection: m.subcollection,
                category: m.category,
                scales: m.scales,
                tags: m.tags,
                images,
            }))
        })
        .await?;
    m.map(Json).ok_or_else(|| ApiError::not_found(format!("no model {id}")))
}

/// The editor's form. Empty strings clear; lookups are created by name.
#[derive(Deserialize, ToSchema, Default)]
#[serde(default, deny_unknown_fields)]
pub struct EditorSave {
    pub title: String,
    pub creator: String,
    pub source: String,
    /// `YYYY-MM`, `YYYY_MM`, `YYYY` or empty.
    pub date: String,
    pub presupports: String,
    pub url: String,
    pub description: String,
    /// 0 clears.
    pub sequence: i64,
    pub release: String,
    pub sculptor: String,
    pub collection: String,
    pub subcollection: String,
    pub category: String,
    pub scales: Vec<String>,
    pub tags: Vec<String>,
}

/// Save the editor's form for one model.
#[utoipa::path(put, path = "/api/models/{id}/editor", tag = "editing", request_body = EditorSave,
    params(("id" = i64, Path, description = "Model id")),
    responses((status = 204), (status = 400, body = crate::error::ErrorBody), (status = 404, body = crate::error::ErrorBody)))]
pub async fn save_editor(
    State(lib): State<AppState>,
    me: CurrentUser,
    Path(id): Path<i64>,
    Json(f): Json<EditorSave>,
) -> Result<StatusCode, ApiError> {
    editor(&me)?;
    let form = EditorForm {
        title: f.title,
        creator: f.creator,
        source: f.source,
        date: f.date,
        presupports: f.presupports,
        url: f.url,
        description: f.description,
        sequence: f.sequence,
        release: f.release,
        sculptor: f.sculptor,
        collection: f.collection,
        subcollection: f.subcollection,
        category: f.category,
        scales: f.scales,
        tags: f.tags,
    };
    let saved = lib.run(move |lib| Ok(lib.db.with_tx(|tx| model_edit::save_model_editor(tx, id, &form))?)).await?;
    match saved {
        None => Err(ApiError::not_found(format!("no model {id}"))),
        Some(path) => {
            lib.schedule_sidecars(path);
            Ok(StatusCode::NO_CONTENT)
        }
    }
}

/// Completion lists: `titles`, `creators`, `sources`, `dates`, `tags`,
/// `releases`, `sculptors`, `collections`, `subcollections`, `categories`, `scales`.
#[utoipa::path(get, path = "/api/editor/choices", tag = "editing", responses((status = 200, body = Object)))]
pub async fn choices(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<BTreeMap<String, Vec<String>>>, ApiError> {
    let c = lib.run(|lib| Ok(lib.db.with(model_edit::editor_choices)?)).await?;
    Ok(Json(c.into_iter().map(|(k, v)| (k.to_string(), v)).collect()))
}

#[derive(Deserialize, utoipa::IntoParams)]
pub struct CollectionQuery {
    pub collection: String,
}

/// The subcollection names of one collection.
#[utoipa::path(get, path = "/api/editor/subcollections", tag = "editing", params(CollectionQuery),
    responses((status = 200, body = Vec<String>)))]
pub async fn subcollections(
    State(lib): State<AppState>,
    _me: CurrentUser,
    Query(q): Query<CollectionQuery>,
) -> Result<Json<Vec<String>>, ApiError> {
    Ok(Json(lib.run(move |lib| Ok(lib.db.with(|c| model_edit::subcollection_names(c, &q.collection))?)).await?))
}

// ── mass editor ──────────────────────────────────────────────────────────────

/// Fields the mass editor sets directly, in the order they are applied.
const MANUAL_FIELDS: &[&str] = &["source", "creator", "release_date", "url", "title"];

#[derive(Deserialize, Serialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct FindReplace {
    pub find: String,
    pub replace: String,
    /// Case-sensitive matching.
    #[serde(default)]
    pub case: bool,
    /// `find` is a regular expression (Rust fancy-regex); `replace` then takes
    /// `\1`, `\g<name>`, `\n` as in Python.
    #[serde(default)]
    pub regex: bool,
    /// Any of `title`, `creator`, `source`, `url`.
    pub fields: Vec<String>,
}

#[derive(Deserialize, ToSchema, Default)]
#[serde(default, deny_unknown_fields)]
pub struct MassEdit {
    pub ids: Vec<i64>,
    /// A path pattern (`{sculptor}/{release}/{title}`) parsed from each
    /// model's archive path; its values are applied per model.
    pub pattern: String,
    /// Path values replace existing ones (otherwise they fill empty fields).
    pub overwrite: bool,
    /// `source`, `creator`, `release_date`, `url`, `title` → template
    /// (`{token}`s from the path pattern and file name), or null to clear.
    pub manual: BTreeMap<String, Option<String>>,
    /// Fields to clear.
    pub clearing: Vec<String>,
    pub scale: String,
    /// Null: no change; empty: clear.
    pub presupports: Option<String>,
    pub release: String,
    pub collection: String,
    pub subcollection: String,
    pub category: String,
    pub sculptor: String,
    /// Comma-separated tags to add (a template).
    pub tags: String,
    pub find: Option<FindReplace>,
    /// Strip leading and trailing whitespace from the text fields.
    pub trim: bool,
}

#[derive(Serialize, ToSchema)]
pub struct MassResult {
    pub models: usize,
    /// Models whose archive path matched `pattern`.
    pub pattern_matches: usize,
}

/// Apply the mass editor's changes to the listed models in one transaction.
#[utoipa::path(post, path = "/api/models/mass-edit", tag = "editing", request_body = MassEdit,
    responses((status = 200, body = MassResult), (status = 400, body = crate::error::ErrorBody)))]
pub async fn mass_edit(State(lib): State<AppState>, me: CurrentUser, Json(m): Json<MassEdit>) -> Result<Json<MassResult>, ApiError> {
    editor(&me)?;
    if let Some(k) = m.manual.keys().find(|k| !MANUAL_FIELDS.contains(&k.as_str())) {
        return Err(ApiError::bad_request(format!("unknown field {k:?}")));
    }
    let (result, paths) = lib
        .run(move |lib| {
            let paths = lib.db.with(|c| model_edit::archive_paths(c, &m.ids))?;
            let pattern = m.pattern.trim().to_string();
            let mut path_results = HashMap::new();
            if !pattern.is_empty() {
                for &id in &m.ids {
                    if let Some(p) = paths.get(&id) {
                        let fields = stl_core::path_pattern::parse_path(&pattern, p);
                        if !fields.is_empty() {
                            path_results.insert(id, fields);
                        }
                    }
                }
            }
            let pattern_matches = path_results.len();
            let manual = MANUAL_FIELDS
                .iter()
                .filter_map(|f| m.manual.get(*f).map(|v| (f.to_string(), v.clone())))
                .collect();
            let plan = MassPlan {
                model_ids: m.ids,
                path_results,
                overwrite: m.overwrite,
                manual,
                clearing: m.clearing.into_iter().collect::<BTreeSet<_>>(),
                scale: m.scale,
                presupports: m.presupports,
                release: m.release,
                collection: m.collection,
                subcollection: m.subcollection,
                category: m.category,
                sculptor: m.sculptor,
                tags: m.tags,
                find: m.find.map(|f| Find { find: f.find, replace: f.replace, case: f.case, regex: f.regex, fields: f.fields }),
                trim: m.trim,
            };
            lib.db.with_tx(|tx| model_edit::mass_save(tx, &plan))?;
            Ok((MassResult { models: paths.len(), pattern_matches }, paths.into_values().collect::<Vec<_>>()))
        })
        .await?;
    lib.schedule_sidecars(paths);
    Ok(Json(result))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct ParseRequest {
    pub ids: Vec<i64>,
    pub pattern: String,
}

#[derive(Serialize, ToSchema)]
pub struct Parsed {
    pub id: i64,
    pub archive_path: String,
    /// What the pattern extracted (`title`, `sculptor_name`, `release_name`, …), in
    /// pattern order; empty when the path does not match.
    pub fields: Vec<(String, String)>,
}

/// What a path pattern extracts from each model's archive path (the mass
/// editor's "Show parsed values"); nothing is saved.
#[utoipa::path(post, path = "/api/models/parse-paths", tag = "editing", request_body = ParseRequest,
    responses((status = 200, body = Vec<Parsed>)))]
pub async fn parse_paths(State(lib): State<AppState>, _me: CurrentUser, Json(req): Json<ParseRequest>) -> Result<Json<Vec<Parsed>>, ApiError> {
    if req.ids.len() > 10_000 {
        return Err(ApiError::bad_request("at most 10000 models"));
    }
    let out = lib
        .run(move |lib| {
            let paths = lib.db.with(|c| model_edit::archive_paths(c, &req.ids))?;
            let pattern = req.pattern.trim().to_string();
            Ok(req
                .ids
                .iter()
                .filter_map(|id| paths.get(id).map(|p| (*id, p.clone())))
                .map(|(id, archive_path)| {
                    let fields = if pattern.is_empty() { Vec::new() } else { stl_core::path_pattern::parse_path(&pattern, &archive_path) };
                    Parsed { id, archive_path, fields }
                })
                .collect())
        })
        .await?;
    Ok(Json(out))
}

// ── removing models ──────────────────────────────────────────────────────────

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct IdsRequest {
    pub ids: Vec<i64>,
}

/// Remove models from the library (their images and thumbnails too). The
/// archive files are not touched.
#[utoipa::path(post, path = "/api/models/delete", tag = "editing", request_body = IdsRequest,
    responses((status = 200, body = Changed), (status = 403, body = crate::error::ErrorBody)))]
pub async fn delete_models(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<IdsRequest>) -> Result<Json<Changed>, ApiError> {
    editor(&me)?;
    let paths = lib
        .run(move |lib| {
            let paths = lib.db.with(|c| model_edit::archive_paths(c, &req.ids))?;
            let refs = lib.db.with_tx(|tx| browser::delete_models(tx, &req.ids))?;
            delete_thumbnails(lib, &refs)?;
            Ok(paths.into_values().collect::<Vec<_>>())
        })
        .await?;
    let models = paths.len();
    lib.schedule_sidecars(paths);
    Ok(Json(Changed { models }))
}

/// Drop thumbnail references (`images.thumbnail_path`: the image id) from the
/// store, after the database change committed.
fn delete_thumbnails(lib: &Library, refs: &[String]) -> Result<(), ApiError> {
    let ids: Vec<u64> = refs.iter().filter_map(|r| r.trim().parse().ok()).collect();
    lib.thumbs.delete_many(&ids)?;
    Ok(())
}

// ── images ───────────────────────────────────────────────────────────────────

/// Largest upload accepted per image.
pub const MAX_IMAGE_BYTES: usize = 64 * 1024 * 1024;

#[derive(Serialize, ToSchema)]
pub struct AddedImages {
    pub added: Vec<ImageRef>,
    /// File names that could not be read as images.
    pub failed: Vec<String>,
}

/// Add images to a model (multipart form, one file per part). Each becomes a
/// thumbnail (resized to the library's thumbnail size); the original is not kept.
#[utoipa::path(post, path = "/api/models/{id}/images", tag = "editing",
    params(("id" = i64, Path, description = "Model id")),
    request_body(content_type = "multipart/form-data", description = "Image files"),
    responses((status = 200, body = AddedImages), (status = 404, body = crate::error::ErrorBody)))]
pub async fn add_images(
    State(lib): State<AppState>,
    me: CurrentUser,
    Path(id): Path<i64>,
    mut form: Multipart,
) -> Result<Json<AddedImages>, ApiError> {
    editor(&me)?;
    let mut files: Vec<(String, Vec<u8>)> = Vec::new();
    while let Some(field) = form.next_field().await.map_err(|e| ApiError::bad_request(e.to_string()))? {
        let name = field.file_name().unwrap_or("image").to_string();
        let data = field.bytes().await.map_err(|e| ApiError::bad_request(e.to_string()))?;
        if data.len() > MAX_IMAGE_BYTES {
            return Err(ApiError::new(StatusCode::PAYLOAD_TOO_LARGE, format!("{name} is larger than 64 MB")));
        }
        files.push((name, data.to_vec()));
    }
    let (result, path) = lib
        .run(move |lib| {
            let path = lib.db.with(|c| model_edit::archive_paths(c, &[id]))?.remove(&id);
            let Some(path) = path else { return Err(ApiError::not_found(format!("no model {id}"))) };
            let size = LibrarySettings::load(&lib.config.data_dir).max_thumb_size();
            let mut thumbs = Vec::new();
            let mut failed = Vec::new();
            for (name, data) in files {
                match stl_core::thumbnail::make_thumbnail(&data, size, true) {
                    Ok(t) => thumbs.push((name, t)),
                    Err(_) => failed.push(name),
                }
            }
            let names: Vec<String> = thumbs.iter().map(|(n, _)| n.clone()).collect();
            let ids = lib.db.with_tx(|tx| browser::add_images(tx, id, &names))?;
            let items: Vec<(u64, &[u8])> = ids.iter().zip(&thumbs).map(|(&i, (_, t))| (i as u64, t.as_slice())).collect();
            if let Err(e) = lib.thumbs.put_many(&items) {
                lib.db.with_tx(|tx| maintenance::delete_images(tx, &ids))?;
                return Err(e.into());
            }
            let refs: Vec<(i64, String)> = ids.iter().map(|&i| (i, i.to_string())).collect();
            lib.db.with_tx(|tx| browser::set_image_thumbnails(tx, &refs))?;
            let digests: HashMap<u64, Option<Vec<u8>>> =
                lib.thumbs.digests(&ids.iter().map(|&i| i as u64).collect::<Vec<_>>())?.into_iter().collect();
            let added = ids
                .iter()
                .map(|&i| ImageRef { id: i, thumbnail: digests.get(&(i as u64)).map(|d| thumbnail_url(i, d.as_deref())) })
                .collect();
            Ok((AddedImages { added, failed }, path))
        })
        .await?;
    lib.schedule_sidecars([path]);
    Ok(Json(result))
}

/// Remove one image and its thumbnail.
#[utoipa::path(delete, path = "/api/images/{id}", tag = "editing",
    params(("id" = i64, Path, description = "Image id")),
    responses((status = 204), (status = 404, body = crate::error::ErrorBody)))]
pub async fn delete_image(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>) -> Result<StatusCode, ApiError> {
    editor(&me)?;
    let path = lib
        .run(move |lib| {
            let path: Option<Option<String>> = lib.db.with(|c| {
                use rusqlite::OptionalExtension;
                c.query_row(
                    "SELECT m.archive_path FROM images i JOIN models m ON m.id = i.model_id WHERE i.id = ?1",
                    [id],
                    |r| r.get(0),
                )
                .optional()
            })?;
            let Some(path) = path else { return Ok(None) };
            lib.db.with_tx(|tx| maintenance::delete_images(tx, &[id]))?;
            lib.thumbs.delete_many(&[id as u64])?;
            Ok(Some(path))
        })
        .await?;
    match path {
        None => Err(ApiError::not_found(format!("no image {id}"))),
        Some(p) => {
            lib.schedule_sidecars(p);
            Ok(StatusCode::NO_CONTENT)
        }
    }
}

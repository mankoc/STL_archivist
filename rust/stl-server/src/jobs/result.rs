//! What jobs report (`JobDetail.result`).

use serde::Serialize;
use utoipa::ToSchema;

use crate::dupes::{ContentRow, ImageResult, MetadataGroup};

/// What a finder or maintenance job found (`JobDetail.result`).
#[derive(Debug, Clone, Serialize, ToSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum JobResult {
    Content { pairs: Vec<ContentRow> },
    Images(ImageResult),
    Metadata { groups: Vec<MetadataGroup> },
    /// Records of a maintenance list (Verify, duplicate entries, moved, found).
    Records { rows: Vec<crate::maint::RecordRow> },
    /// The AI tagger's suggestion for one model (`ai_suggest`).
    AiSuggestion(Box<crate::ai::AiSuggestion>),
    /// The editor fields the model inferred for one model (`ai_fields`).
    AiFields(Box<crate::ai::AiEditorFields>),
    /// What moving a library to a new root found (`relocate_library`).
    Relocation(Relocation),
    /// A package's folders and where their archives are here (`import_library`).
    Import(ImportPreview),
}

/// A library package checked (and imported) by `import_library`.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct ImportPreview {
    /// The package (a name in the backups list).
    pub name: String,
    /// Its outermost folders; the folders inside them move along.
    pub folders: Vec<ImportFolder>,
    /// Records in the package.
    pub models: i64,
    /// It replaced this server's library (else it was only checked).
    pub applied: bool,
}

/// One of a package's outermost folders.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct ImportFolder {
    pub uuid: String,
    pub name: String,
    /// The library it is a folder of.
    pub library: String,
    /// Its root where the package was made.
    pub root: String,
    /// Its root here: the one given, else the old one (kept when nothing maps it).
    pub new_root: String,
    /// `new_root` is a folder on this server.
    pub root_exists: bool,
    /// Records in it and the folders inside it.
    pub models: i64,
    /// Folders inside it, moved along.
    pub inner: usize,
    /// Archives found under `new_root`.
    pub found: usize,
    /// Found only with another letter case.
    pub case_only: usize,
    pub missing: usize,
    /// Some relative paths not found.
    pub missing_examples: Vec<String>,
}

/// The check (and move) of a library to a new root.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct Relocation {
    pub library: i64,
    pub root: String,
    pub total: usize,
    pub found: usize,
    /// Found only with another letter case.
    pub case_only: usize,
    pub missing: usize,
    /// Some relative paths not found.
    pub missing_examples: Vec<String>,
    /// Libraries inside it, moved along.
    pub inner: usize,
    /// The root was changed (else only checked).
    pub applied: bool,
}

impl JobResult {
    /// Pairs or groups found.
    pub fn len(&self) -> usize {
        match self {
            JobResult::Content { pairs } => pairs.len(),
            JobResult::Images(r) => r.pairs.len(),
            JobResult::Metadata { groups } => groups.len(),
            JobResult::Records { rows } => rows.len(),
            JobResult::AiSuggestion(_) | JobResult::AiFields(_) => 1,
            JobResult::Relocation(r) => r.total,
            JobResult::Import(r) => r.folders.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

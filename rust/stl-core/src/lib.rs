//! StlArchivist core library.
//!
//! Everything the application does without a user interface: storage, archive
//! reading, hashing, the crawler and the services. It has no UI or HTTP
//! dependency; `stl-server` puts both on top.

pub mod archive;
pub mod avif;
pub mod ai_tagging;
pub mod backup;
pub mod clip;
pub mod control;
pub mod crawler;
pub mod dhash;
pub mod embedding;
pub mod fingerprint;
pub mod fuzzy;
pub mod hamming;
pub mod image_pairs;
pub mod lock;
pub mod metadata;
pub mod monitor;
pub mod path_pattern;
pub mod pyunicode;
pub mod relpath;
pub mod pairs;
pub mod rescan;
pub mod rules;
pub mod sort_files;
pub mod storage;
pub mod strings;
pub mod thumbstore;
pub mod thumbnail;
pub mod view;
pub mod winpath;

/// Version of the core library.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");



//! stlLibrary core library.
//!
//! Everything the application does without a user interface: storage, archive
//! reading, hashing, the crawler and the services. It has no Python, UI or HTTP
//! dependency, so the same code backs the PyO3 module used by the current PyQt
//! app (`stl-py`) and, later, the server. See `docs/rust-migration-plan.md`.

pub mod archive;
pub mod control;
pub mod crawler;
pub mod dhash;
pub mod embedding;
pub mod fingerprint;
pub mod hamming;
pub mod metadata;
pub mod path_pattern;
pub mod pyunicode;
pub mod pyjson;
pub mod pairs;
pub mod rules;
pub mod storage;
pub mod strings;
pub mod thumbstore;
pub mod thumbnail;
pub mod winpath;

/// Version of the core library.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");



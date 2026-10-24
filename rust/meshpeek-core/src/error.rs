//! The error type of all loaders.

use std::path::PathBuf;

/// Everything that can go wrong while reading a file.
#[derive(Debug, thiserror::Error)]
pub enum LoadError {
    /// The file could not be read.
    #[error("{path}: {source}")]
    Io {
        /// The file that failed.
        path: PathBuf,
        /// What the operating system reported.
        source: std::io::Error,
    },
    /// The file was read but its contents are not valid for its format.
    #[error("{0}")]
    Format(String),
    /// No loader handles this file (the message names the extension).
    #[error("unsupported file type: {0}")]
    Unsupported(String),
}

/// Result of a loader.
pub type Result<T> = std::result::Result<T, LoadError>;

impl LoadError {
    /// A [`LoadError::Format`] with `message`.
    pub fn format(message: impl Into<String>) -> Self {
        Self::Format(message.into())
    }

    /// A [`LoadError::Io`] for `path`.
    pub fn io(path: impl Into<PathBuf>, source: std::io::Error) -> Self {
        Self::Io { path: path.into(), source }
    }
}

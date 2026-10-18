//! The library's `settings.json` (shared with the desktop app, compatibility
//! rule 6): read on each use, so changes made elsewhere apply at once.

use std::path::Path;

use serde_json::Value;

pub const FILE: &str = "settings.json";
pub const MAX_THUMB_SIZE_DEFAULT: u32 = 512;

pub struct LibrarySettings(Value);

impl LibrarySettings {
    /// The settings in `data_dir` (empty when the file is missing or unreadable).
    pub fn load(data_dir: &Path) -> Self {
        let v = std::fs::read_to_string(data_dir.join(FILE))
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or(Value::Null);
        LibrarySettings(v)
    }

    fn get_bool(&self, key: &str, default: bool) -> bool {
        self.0.get(key).and_then(Value::as_bool).unwrap_or(default)
    }

    pub fn max_thumb_size(&self) -> u32 {
        self.0
            .get("max_thumb_size")
            .and_then(Value::as_u64)
            .and_then(|n| u32::try_from(n).ok())
            .filter(|&n| n > 0)
            .unwrap_or(MAX_THUMB_SIZE_DEFAULT)
    }

    pub fn sidecars_enabled(&self) -> bool {
        self.get_bool("metadata_sidecar_enabled", true)
    }

    pub fn sidecar_thumbnails(&self) -> bool {
        self.get_bool("metadata_thumbnails", true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_and_values() {
        let dir = tempfile::tempdir().unwrap();
        let s = LibrarySettings::load(dir.path());
        assert_eq!((s.max_thumb_size(), s.sidecars_enabled(), s.sidecar_thumbnails()), (512, true, true));
        std::fs::write(dir.path().join(FILE), r#"{"max_thumb_size": 256, "metadata_sidecar_enabled": false}"#).unwrap();
        let s = LibrarySettings::load(dir.path());
        assert_eq!((s.max_thumb_size(), s.sidecars_enabled(), s.sidecar_thumbnails()), (256, false, true));
    }
}

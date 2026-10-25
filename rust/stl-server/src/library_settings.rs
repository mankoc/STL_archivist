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

    /// Longest edge of a thumbnail made from a model viewer capture; default: [`Self::max_thumb_size`].
    pub fn view_thumb_size(&self) -> u32 {
        self.0
            .get("view_thumb_size")
            .and_then(Value::as_u64)
            .and_then(|n| u32::try_from(n).ok())
            .filter(|&n| n > 0)
            .unwrap_or_else(|| self.max_thumb_size())
    }

    pub fn sidecars_enabled(&self) -> bool {
        self.get_bool("metadata_sidecar_enabled", true)
    }

    pub fn sidecar_thumbnails(&self) -> bool {
        self.get_bool("metadata_thumbnails", true)
    }

    pub fn max_thumbnails(&self) -> usize {
        self.0.get("max_thumbnails").and_then(Value::as_u64).map_or(MAX_THUMBS_DEFAULT, |n| n as usize)
    }

    /// Default: half the logical CPUs, clamped to 2..=8 (`_platform_defaults`).
    pub fn max_workers(&self) -> usize {
        let cpus = std::thread::available_parallelism().map_or(2, |n| n.get());
        self.0
            .get("max_workers")
            .and_then(Value::as_u64)
            .map_or_else(|| (cpus / 2).clamp(2, 8), |n| (n as usize).max(1))
    }

    pub fn recurse_subdirs(&self) -> bool {
        self.get_bool("recurse_subdirs", true)
    }

    pub fn checks(&self) -> stl_core::rules::Checks {
        stl_core::rules::Checks {
            presupported: self.get_bool("warn_presupported_stl", true),
            no_images: self.get_bool("warn_no_images", true),
            nested_archive: self.get_bool("warn_nested_archive", true),
            no_printable: self.get_bool("warn_no_printable", true),
        }
    }

    pub fn ai_embeddings_enabled(&self) -> bool {
        self.get_bool("ai_embeddings_enabled", true)
    }

    pub fn ai_embeddings_during_crawl(&self) -> bool {
        self.get_bool("ai_embeddings_during_crawl", false)
    }

    /// AI embeddings enabled, and computed during crawls.
    pub fn embed_during_crawl(&self) -> bool {
        self.ai_embeddings_enabled() && self.ai_embeddings_during_crawl()
    }

    pub fn warn_low_compression(&self) -> bool {
        self.get_bool("warn_low_compression", false)
    }

    /// Sort Files may move archives (`get_file_sorting_enabled`).
    pub fn file_sorting_enabled(&self) -> bool {
        self.get_bool("file_sorting_enabled", false)
    }

    /// Delete Files may remove archives from disk (`get_allow_file_delete`).
    pub fn allow_file_delete(&self) -> bool {
        self.get_bool("allow_file_delete", false)
    }

    /// Deleted archives go to the trash folder (`get_use_trash_folder`).
    pub fn use_trash_folder(&self) -> bool {
        self.get_bool("use_trash_folder", true)
    }

    pub fn trash_folder_path(&self) -> String {
        self.0.get("trash_folder_path").and_then(Value::as_str).unwrap_or("").to_string()
    }

    /// The AI tagger's sampling temperature (`get_ai_temperature`).
    pub fn ai_temperature(&self) -> f64 {
        self.0.get("ai_temperature").and_then(Value::as_f64).unwrap_or(0.2)
    }

    /// How often monitored folders are checked (minutes).
    pub fn monitor_interval_minutes(&self) -> u32 {
        self.0
            .get("monitor_interval_minutes")
            .and_then(Value::as_u64)
            .and_then(|n| u32::try_from(n).ok())
            .filter(|n| (1..=MONITOR_INTERVAL_MAX).contains(n))
            .unwrap_or(MONITOR_INTERVAL_DEFAULT)
    }

    pub fn low_compression_threshold(&self) -> f64 {
        self.0.get("low_compression_threshold").and_then(Value::as_f64).unwrap_or(0.95)
    }

    /// The presupport filename patterns (`get_presupport_patterns`): the saved
    /// list, or the defaults when none is saved or it is the retired default.
    pub fn presupport_patterns(&self) -> Vec<String> {
        let defaults = || stl_core::rules::DEFAULT_PRESUPPORT_PATTERNS.iter().map(|s| s.to_string()).collect();
        let Some(saved) = self.0.get("presupport_patterns").and_then(Value::as_array) else { return defaults() };
        let saved: Vec<String> = saved.iter().filter_map(|v| v.as_str().map(str::to_string)).collect();
        if saved == LEGACY_PRESUPPORT_PATTERNS { defaults() } else { saved }
    }
}

pub const MAX_THUMBS_DEFAULT: usize = 5;
pub const MONITOR_INTERVAL_DEFAULT: u32 = 30;
/// A day.
pub const MONITOR_INTERVAL_MAX: u32 = 1440;

/// `rules.LEGACY_PRESUPPORT_PATTERNS`: a saved list equal to it is upgraded to the defaults.
const LEGACY_PRESUPPORT_PATTERNS: [&str; 3] = [r"(?<!un)[ _-]supported\.stl$", r"^sup_.*\.stl$", r"^(?:s|\[s\])_.*\.stl$"];

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

    #[test]
    fn presupport_patterns_upgrade_the_legacy_default() {
        let dir = tempfile::tempdir().unwrap();
        let defaults: Vec<String> = stl_core::rules::DEFAULT_PRESUPPORT_PATTERNS.iter().map(|s| s.to_string()).collect();
        assert_eq!(LibrarySettings::load(dir.path()).presupport_patterns(), defaults);
        let legacy = serde_json::json!({"presupport_patterns": LEGACY_PRESUPPORT_PATTERNS});
        std::fs::write(dir.path().join(FILE), legacy.to_string()).unwrap();
        assert_eq!(LibrarySettings::load(dir.path()).presupport_patterns(), defaults);
        std::fs::write(dir.path().join(FILE), r#"{"presupport_patterns": ["_sup\\.stl$"]}"#).unwrap();
        assert_eq!(LibrarySettings::load(dir.path()).presupport_patterns(), vec![r"_sup\.stl$".to_string()]);
    }
}

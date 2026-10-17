//! Progress reporting and cancellation for long-running operations.
//!
//! Worker threads update a shared [`Control`]; whoever started the job (the
//! PyO3 binding today, a server job later) polls [`Control::progress`] and calls
//! [`Control::request_stop`]. No callback crosses into a worker thread, so the
//! core never needs the Python GIL or an async runtime.

use std::fmt;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

/// Shared progress counter and stop flag for one job.
#[derive(Debug, Default)]
pub struct Control {
    done: AtomicU64,
    total: AtomicU64,
    stop: AtomicBool,
}

/// Returned by an operation that stopped because [`Control::request_stop`] was called.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Stopped;

impl fmt::Display for Stopped {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("operation stopped")
    }
}

impl std::error::Error for Stopped {}

impl Control {
    pub fn new() -> Self {
        Self::default()
    }

    /// Set the amount of work the job will report (resets progress).
    pub fn set_total(&self, total: u64) {
        self.done.store(0, Ordering::Relaxed);
        self.total.store(total, Ordering::Relaxed);
    }

    /// Record `n` more units of work done.
    pub fn advance(&self, n: u64) {
        self.done.fetch_add(n, Ordering::Relaxed);
    }

    /// `(done, total)`; `done` never exceeds `total`.
    pub fn progress(&self) -> (u64, u64) {
        let total = self.total.load(Ordering::Relaxed);
        (self.done.load(Ordering::Relaxed).min(total), total)
    }

    pub fn request_stop(&self) {
        self.stop.store(true, Ordering::Relaxed);
    }

    pub fn is_stopped(&self) -> bool {
        self.stop.load(Ordering::Relaxed)
    }

    /// `Err(Stopped)` once a stop has been requested.
    pub fn check(&self) -> Result<(), Stopped> {
        if self.is_stopped() { Err(Stopped) } else { Ok(()) }
    }
}

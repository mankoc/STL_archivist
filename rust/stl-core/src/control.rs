//! Progress reporting and cancellation for long-running operations.
//!
//! Worker threads update a shared [`Control`]; whoever started the job polls
//! [`Control::progress`] and calls [`Control::request_stop`]. No callback crosses
//! into a worker thread, so the core never needs an async runtime.
//!
//! A paused operation waits at its next checkpoint ([`Control::is_stopped`] or
//! [`Control::check`]) until it is resumed or stopped.

use std::fmt;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;

/// How often a paused checkpoint looks whether it may go on.
const PAUSE_POLL: Duration = Duration::from_millis(200);

/// Shared progress counter and stop and pause flags for one job.
#[derive(Debug, Default)]
pub struct Control {
    done: AtomicU64,
    total: AtomicU64,
    stop: AtomicBool,
    pause: AtomicBool,
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

    /// Whether a stop was requested, without waiting while paused (for
    /// checks made with a lock held, or in the middle of a transfer).
    pub fn stop_requested(&self) -> bool {
        self.stop.load(Ordering::Relaxed)
    }

    /// A checkpoint: whether to stop here. While paused, it waits until the
    /// operation is resumed or stopped.
    pub fn is_stopped(&self) -> bool {
        while self.is_paused() && !self.stop_requested() {
            std::thread::sleep(PAUSE_POLL);
        }
        self.stop_requested()
    }

    pub fn set_paused(&self, paused: bool) {
        self.pause.store(paused, Ordering::Relaxed);
    }

    pub fn is_paused(&self) -> bool {
        self.pause.load(Ordering::Relaxed)
    }

    /// `Err(Stopped)` once a stop has been requested.
    pub fn check(&self) -> Result<(), Stopped> {
        if self.is_stopped() { Err(Stopped) } else { Ok(()) }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::time::Instant;

    #[test]
    fn a_paused_checkpoint_waits_until_resumed_or_stopped() {
        let ctl = Arc::new(Control::new());
        ctl.set_paused(true);
        assert!(!ctl.stop_requested(), "only checkpoints wait");
        let waiter = {
            let ctl = Arc::clone(&ctl);
            std::thread::spawn(move || {
                let start = Instant::now();
                (ctl.is_stopped(), start.elapsed())
            })
        };
        std::thread::sleep(Duration::from_millis(500));
        ctl.set_paused(false);
        let (stopped, waited) = waiter.join().unwrap();
        assert!(!stopped);
        assert!(waited >= Duration::from_millis(400), "{waited:?}");

        ctl.set_paused(true);
        ctl.request_stop();
        assert_eq!(ctl.check(), Err(Stopped), "a stop ends the pause");
    }
}

"""Simple background job registry used to prevent concurrent heavy tasks.

Jobs are identified by a short string name (e.g. 'crawler', 'thumbnails').
start_job(name) returns True when the job was registered successfully and
False when some other job is already active (no concurrent jobs allowed).
This is intentionally simple — extend later if you want a queue.
"""
from threading import Lock

_lock = Lock()
_active_job: str | None = None


def start_job(name: str) -> bool:
    """Attempt to start a background job.

    Returns True if started (no other active job), False if another job is active.
    """
    global _active_job
    with _lock:
        if _active_job is not None:
            return False
        _active_job = name
        return True


def end_job(name: str) -> None:
    """Mark the job as finished; silently ignore if name doesn't match."""
    global _active_job
    with _lock:
        if _active_job == name:
            _active_job = None


def active_job() -> str | None:
    """Return the name of the currently active job, or None if idle."""
    with _lock:
        return _active_job

"""Notification sound playback.

All imports are deferred so this module is safe to import at module level,
before QApplication exists.  Call :func:`play_notification` at the end of
any long operation to play the configured sound (if enabled for that group).
"""
import logging
import sys

log = logging.getLogger(__name__)

# Groups correspond to the per-operation checkboxes in Configuration → Notifications.
GROUPS = ("crawl", "duplicates", "maintenance", "sidecar", "backup")

# Strong reference kept so QSoundEffect isn't GC'd before the sound finishes.
_current_effect = None


def play_notification(group: str) -> None:
    """Play the configured notification sound if enabled for *group*.

    *group* is one of: ``"crawl"``, ``"duplicates"``, ``"maintenance"``,
    ``"sidecar"``, ``"backup"``.  Silently no-ops on any error.
    """
    try:
        from app.ui.config_dialog import (
            get_notifications_enabled,
            get_notification_sound,
            get_notification_sound_file,
        )
        import importlib
        mod = importlib.import_module("app.ui.config_dialog")
        per_group_getter = getattr(mod, f"get_notify_{group}", lambda: True)

        if not get_notifications_enabled() or not per_group_getter():
            return

        sound = get_notification_sound()
        if sound == "custom":
            path = get_notification_sound_file()
            if path:
                _play_file(path)
                return
        _play_beep()
    except Exception:
        log.debug("Notification sound error", exc_info=True)


def play_test_sound(sound: str, file_path: str) -> None:
    """Play a sound immediately for the Test button in the config dialog."""
    try:
        if sound == "custom" and file_path:
            _play_file(file_path)
        else:
            _play_beep()
    except Exception:
        log.debug("Test sound error", exc_info=True)


def _play_beep() -> None:
    from PyQt6.QtWidgets import QApplication
    QApplication.beep()


def _play_file(path: str) -> None:
    """Play *path* via the first available backend.

    Fallback cascade: ``winsound`` (Windows) → ``QSoundEffect`` (QtMultimedia,
    cross-platform) → subprocess players (``paplay``/``aplay``/``afplay`` on
    Linux/macOS) → ``QApplication.beep()`` as a last resort.
    """
    global _current_effect
    if sys.platform == "win32":
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        except Exception as exc:
            log.debug("winsound failed (%s), trying QSoundEffect", exc)
    # Non-Windows or winsound failed: try QtMultimedia
    try:
        from PyQt6.QtMultimedia import QSoundEffect
        from PyQt6.QtCore import QUrl
        eff = QSoundEffect()
        eff.setSource(QUrl.fromLocalFile(path))
        eff.play()
        _current_effect = eff          # prevent GC before playback ends
        return
    except ImportError:
        pass
    # Final fallback: subprocess audio players
    import subprocess
    for cmd in (["paplay", path], ["aplay", path], ["afplay", path]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    _play_beep()

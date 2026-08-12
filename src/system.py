"""Small platform-dependent helpers, isolated so the rest of the app is portable."""
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def open_path(path) -> bool:
    """
    Open a file or folder in the desktop's default handler.

    ``os.startfile`` only exists on Windows; calling it unguarded is how a
    cross-platform app ends up crashing the moment someone exports a report.
    """
    target = Path(path)
    if not target.exists():
        return False
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606 — the documented Windows API
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except OSError:
        return False


def data_dir(app_name: str = "WorkTrack") -> Path:
    """Per-user application data directory, created if missing."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    directory = base / app_name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resource_path(relative: str) -> Optional[Path]:
    """
    Locate a bundled asset, whether running from source or from a PyInstaller
    executable (which unpacks its data into ``sys._MEIPASS``).
    """
    roots = []
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        roots.append(Path(bundled))
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return None


class SingleInstance:
    """
    A lock that keeps a second copy of the app from opening.

    Two instances would share one SQLite file and one timer-state row, and would
    overwrite each other's idea of what is running.  The lock file is held open
    for the life of the process; the OS releases it if the process dies.
    """

    def __init__(self, lock_path: Path):
        self.lock_path = Path(lock_path)
        self._handle = None

    def acquire(self) -> bool:
        try:
            # Deliberately not a context manager: the handle must stay open
            # for the life of the process, because closing it frees the lock.
            self._handle = open(self.lock_path, "a+")  # noqa: SIM115
        except OSError:
            return True  # cannot lock — better to run than to refuse to start

        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._handle.close()
            self._handle = None
            return False

        return True

    def release(self):
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None

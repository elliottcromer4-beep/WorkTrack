"""
Backups.

A backup is a JSON snapshot of every project, task, session and setting.  JSON
rather than a copy of the database file because it stays readable and
importable if this app is ever gone — the data belongs to whoever recorded it.
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .database import SCHEMA_VERSION, Database
from .timeutil import iso_utc, now_local, now_utc


class BackupManager:
    #: Older automatic backups beyond this count are pruned.
    MAX_BACKUPS = 30

    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.backup_dir = Path(data_dir) / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    # ── Creating ──────────────────────────────────────────────────────────

    def make_backup(self, label: str = "auto") -> Path:
        stamp = now_local().strftime("%Y%m%d_%H%M%S_%f")
        stamp += f"_{time.monotonic_ns():020d}"
        path = self.backup_dir / f"worktrack_{label}_{stamp}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.db.export_all(), indent=2), encoding="utf-8")
        temporary.replace(path)
        self._prune()
        return path

    def make_daily_backup(self) -> Optional[Path]:
        """Back up once a day, on the first launch of that day."""
        today = now_local().strftime("%Y%m%d")
        if list(self.backup_dir.glob(f"worktrack_auto_{today}*.json")):
            return None
        return self.make_backup("auto")

    def list_backups(self) -> list[Path]:
        return sorted(self.backup_dir.glob("worktrack_*.json"),
                      key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)

    def _prune(self):
        automatic = [p for p in self.list_backups()
                     if p.name.startswith("worktrack_auto_")]
        for stale in automatic[self.MAX_BACKUPS:]:
            try:
                stale.unlink()
            except OSError:
                pass

    # ── Restoring ─────────────────────────────────────────────────────────

    def restore_backup(self, backup_path: Path) -> bool:
        """
        Replace the current contents with a backup's.

        The live database file is copied aside first, so a restore of the wrong
        file is itself recoverable.  The rewrite runs as one transaction: if any
        row fails, nothing changes.
        """
        try:
            data = json.loads(Path(backup_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False

        if not self._valid_backup(data):
            return False
        if self.snapshot_database("pre-restore") is None:
            return False

        with self.db._lock:
            conn = self.db._conn
            try:
                conn.execute("BEGIN")
                conn.execute("DELETE FROM timer_state")
                conn.execute("DELETE FROM settings")
                conn.execute("DELETE FROM export_history")
                conn.execute("DELETE FROM sessions")
                conn.execute("DELETE FROM subtasks")
                conn.execute("DELETE FROM projects")

                for project in data.get("projects", []):
                    conn.execute(
                        "INSERT INTO projects"
                        "(id,name,client,notes,color,created_at,archived) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (project["id"], project["name"], project.get("client", ""),
                         project.get("notes", ""), project.get("color", "#4DABF7"),
                         project.get("created_at", iso_utc(now_utc())),
                         project.get("archived", 0)),
                    )
                for task in data.get("subtasks", []):
                    conn.execute(
                        "INSERT INTO subtasks"
                        "(id,project_id,name,created_at,position,deleted) "
                        "VALUES(?,?,?,?,?,?)",
                        (task["id"], task["project_id"], task["name"],
                         task.get("created_at", iso_utc(now_utc())),
                         task.get("position", 0), task.get("deleted", 0)),
                    )
                for session in data.get("sessions", []):
                    conn.execute(
                        "INSERT INTO sessions"
                        "(id,subtask_id,project_id,started_at,ended_at,"
                        "duration_seconds,paused_duration_seconds,notes,is_running) "
                        "VALUES(?,?,?,?,?,?,?,?,0)",
                        (session["id"], session["subtask_id"], session["project_id"],
                         session.get("started_at", ""), session.get("ended_at"),
                         session.get("duration_seconds", 0),
                         session.get("paused_duration_seconds", 0),
                         session.get("notes", "")),
                    )
                for setting in data.get("settings", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                        (setting["key"], setting["value"]),
                    )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                return False

    @staticmethod
    def _valid_backup(data) -> bool:
        """Reject unrelated JSON, missing tables and unsupported backup versions."""
        if not isinstance(data, dict):
            return False
        version = data.get("schema_version")
        if type(version) is not int or not 1 <= version <= SCHEMA_VERSION:
            return False
        required = {
            "projects": {"id", "name"},
            "subtasks": {"id", "project_id", "name"},
            "sessions": {"id", "subtask_id", "project_id", "started_at"},
            "settings": {"key", "value"},
        }
        for table, fields in required.items():
            rows = data.get(table)
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or not fields <= row.keys() for row in rows
            ):
                return False
        tasks = {row["id"]: row["project_id"] for row in data["subtasks"]
                 if isinstance(row["id"], int)}
        return all(isinstance(row["subtask_id"], int)
                   and tasks.get(row["subtask_id"]) == row["project_id"]
                   for row in data["sessions"])

    def snapshot_database(self, label: str) -> Optional[Path]:
        """Copy the live database file aside, keeping its ``.db`` extension."""
        source = Path(self.db.db_path)
        if not source.exists():
            return None
        stamp = now_local().strftime("%Y%m%d_%H%M%S_%f")
        stamp += f"_{time.monotonic_ns():020d}"
        target = self.backup_dir / f"worktrack_{label}_{stamp}.db"
        try:
            with self.db._lock, sqlite3.connect(target) as destination:
                self.db._conn.backup(destination)
            return target
        except (OSError, sqlite3.Error):
            return None

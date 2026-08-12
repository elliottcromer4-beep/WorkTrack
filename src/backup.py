"""
Backups.

A backup is a JSON snapshot of every project, task, session and setting.  JSON
rather than a copy of the database file because it stays readable and
importable if this app is ever gone — the data belongs to whoever recorded it.
"""
import json
import shutil
from pathlib import Path
from typing import Optional

from .database import Database
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
        stamp = now_local().strftime("%Y%m%d_%H%M%S")
        path = self.backup_dir / f"worktrack_{label}_{stamp}.json"
        path.write_text(json.dumps(self.db.export_all(), indent=2), encoding="utf-8")
        self._prune()
        return path

    def make_daily_backup(self) -> Optional[Path]:
        """Back up once a day, on the first launch of that day."""
        today = now_local().strftime("%Y%m%d")
        if list(self.backup_dir.glob(f"worktrack_auto_{today}*.json")):
            return None
        return self.make_backup("auto")

    def list_backups(self) -> list[Path]:
        return sorted(self.backup_dir.glob("worktrack_*.json"), reverse=True)

    def _prune(self):
        for stale in self.list_backups()[self.MAX_BACKUPS:]:
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

        self.snapshot_database("pre-restore")

        with self.db._lock:
            conn = self.db._conn
            try:
                conn.execute("BEGIN")
                conn.execute("DELETE FROM sessions")
                conn.execute("DELETE FROM subtasks")
                conn.execute("DELETE FROM projects")

                for project in data.get("projects", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO projects"
                        "(id,name,client,notes,color,created_at,archived) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (project["id"], project["name"], project.get("client", ""),
                         project.get("notes", ""), project.get("color", "#4DABF7"),
                         project.get("created_at", iso_utc(now_utc())),
                         project.get("archived", 0)),
                    )
                for task in data.get("subtasks", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO subtasks"
                        "(id,project_id,name,created_at,position,deleted) "
                        "VALUES(?,?,?,?,?,?)",
                        (task["id"], task["project_id"], task["name"],
                         task.get("created_at", iso_utc(now_utc())),
                         task.get("position", 0), task.get("deleted", 0)),
                    )
                for session in data.get("sessions", []):
                    conn.execute(
                        "INSERT OR REPLACE INTO sessions"
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

    def snapshot_database(self, label: str) -> Optional[Path]:
        """Copy the live database file aside, keeping its ``.db`` extension."""
        source = Path(self.db.db_path)
        if not source.exists():
            return None
        stamp = now_local().strftime("%Y%m%d_%H%M%S")
        target = self.backup_dir / f"worktrack_{label}_{stamp}.db"
        try:
            self.db.commit()
            shutil.copy2(source, target)
            return target
        except OSError:
            return None

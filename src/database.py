"""
SQLite persistence for WorkTrack.

The connection is shared across the UI thread and the background backup
thread, so every statement runs under a re-entrant lock.  ``check_same_thread``
is disabled to permit the sharing; the lock is what actually makes it safe.
"""
import json
import shutil
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Optional

from .models import Project, Session, Subtask, TimerStateRecord
from .timeutil import iso_utc, now_utc, parse_utc, to_local

# Bumped whenever the on-disk shape or encoding of the data changes.
SCHEMA_VERSION = 2

BASE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

    CREATE TABLE IF NOT EXISTS projects (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL,
        client     TEXT    DEFAULT '',
        notes      TEXT    DEFAULT '',
        color      TEXT    DEFAULT '#4DABF7',
        created_at TEXT    NOT NULL,
        archived   INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS subtasks (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name       TEXT    NOT NULL,
        created_at TEXT    NOT NULL,
        position   INTEGER DEFAULT 0,
        deleted    INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        subtask_id              INTEGER NOT NULL
                                REFERENCES subtasks(id) ON DELETE CASCADE,
        project_id              INTEGER NOT NULL
                                REFERENCES projects(id) ON DELETE CASCADE,
        started_at              TEXT    NOT NULL,
        ended_at                TEXT,
        duration_seconds        REAL    DEFAULT 0,
        paused_duration_seconds REAL    DEFAULT 0,
        notes                   TEXT    DEFAULT '',
        is_running              INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS timer_state (
        id                      INTEGER PRIMARY KEY DEFAULT 1,
        session_id              INTEGER,
        status                  TEXT DEFAULT 'stopped',
        project_id              INTEGER,
        subtask_id              INTEGER,
        started_at              TEXT,
        paused_at               TEXT,
        paused_duration_seconds REAL DEFAULT 0,
        updated_at              TEXT
    );

    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS export_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        export_type TEXT    NOT NULL,
        filename    TEXT    NOT NULL,
        project_ids TEXT    DEFAULT '[]',
        date_from   TEXT,
        date_to     TEXT,
        created_at  TEXT    NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_started  ON sessions(started_at);
    CREATE INDEX IF NOT EXISTS idx_sessions_project  ON sessions(project_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_subtask  ON sessions(subtask_id);
    CREATE INDEX IF NOT EXISTS idx_subtasks_project  ON subtasks(project_id);
"""

DEFAULT_SETTINGS = {
    "worker_name": "",
    "company_name": "",
    "widget_x": "100",
    "widget_y": "100",
    "dashboard_geometry": "1000x700+200+100",
    "rounding_minutes": "0",
    "last_export_dir": "",
}


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # ── Statement helpers ────────────────────────────────────────────────
    #
    # Everything funnels through these three so the lock cannot be forgotten.

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def commit(self):
        with self._lock:
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ── Migrations ───────────────────────────────────────────────────────

    def _migrate(self):
        with self._lock:
            self._conn.executescript(BASE_SCHEMA)
            self._conn.commit()

            row = self._conn.execute(
                "SELECT MAX(version) AS v FROM schema_version"
            ).fetchone()
            current = (row["v"] if row and row["v"] is not None else 0)

            if current < 2:
                self._backup_before_migration()
                self._normalise_timestamps()

            if current < SCHEMA_VERSION:
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_version(version) VALUES(?)",
                    (SCHEMA_VERSION,),
                )

            for key, value in DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value)
                )
            self._conn.commit()

    def _backup_before_migration(self):
        """
        Copy the database aside before a migration rewrites any data.

        Skipped when there is nothing to protect — a fresh install would
        otherwise litter the data folder with a snapshot of an empty database.
        """
        if not self.db_path.exists() or not self._has_recorded_data():
            return
        target = self.db_path.with_name(
            f"{self.db_path.stem}.pre-v{SCHEMA_VERSION}-migration.db"
        )
        if target.exists():
            return
        try:
            self._conn.commit()
            shutil.copy2(self.db_path, target)
        except OSError:
            # A failed safety copy must not stop the app from opening.
            pass

    def _has_recorded_data(self) -> bool:
        for table in ("projects", "sessions"):
            row = self._conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            if row:
                return True
        return False

    def _normalise_timestamps(self):
        """
        Rewrite every stored timestamp into canonical UTC-with-offset form.

        Earlier versions wrote naive ``utcnow()`` strings.  Those are the same
        instants, just missing the offset, so appending it is lossless — but
        making the format uniform is what lets date-range filters compare
        timestamps as strings and still be chronologically correct.
        """
        targets = [
            ("projects", "id", ["created_at"]),
            ("subtasks", "id", ["created_at"]),
            ("sessions", "id", ["started_at", "ended_at"]),
            ("timer_state", "id", ["started_at", "paused_at", "updated_at"]),
            ("export_history", "id", ["created_at"]),
        ]
        for table, key, columns in targets:
            rows = self._conn.execute(
                f"SELECT {key}, {', '.join(columns)} FROM {table}"
            ).fetchall()
            for row in rows:
                updates, values = [], []
                for col in columns:
                    raw = row[col]
                    if not raw:
                        continue
                    parsed = parse_utc(raw)
                    if parsed is None:
                        continue
                    canonical = iso_utc(parsed)
                    if canonical != raw:
                        updates.append(f"{col}=?")
                        values.append(canonical)
                if updates:
                    values.append(row[key])
                    self._conn.execute(
                        f"UPDATE {table} SET {', '.join(updates)} WHERE {key}=?", values
                    )
        self._conn.commit()

    # ── Settings ─────────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        self._execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    # ── Projects ─────────────────────────────────────────────────────────

    def create_project(self, name: str, client: str = "", notes: str = "",
                       color: str = "#4DABF7") -> int:
        cur = self._execute(
            "INSERT INTO projects(name,client,notes,color,created_at) VALUES(?,?,?,?,?)",
            (name, client, notes, color, iso_utc(now_utc())),
        )
        return cur.lastrowid

    def get_projects(self, include_archived: bool = False) -> list[Project]:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived=0"
        sql += " ORDER BY archived, name COLLATE NOCASE"
        return [Project(**dict(r)) for r in self._query(sql)]

    def get_project(self, project_id: int) -> Optional[Project]:
        row = self._query_one("SELECT * FROM projects WHERE id=?", (project_id,))
        return Project(**dict(row)) if row else None

    def update_project(self, project_id: int, name: str, client: str,
                       notes: str, color: str):
        self._execute(
            "UPDATE projects SET name=?,client=?,notes=?,color=? WHERE id=?",
            (name, client, notes, color, project_id),
        )

    def archive_project(self, project_id: int, archived: bool = True):
        self._execute(
            "UPDATE projects SET archived=? WHERE id=?", (int(archived), project_id)
        )

    def delete_project(self, project_id: int):
        self._execute("DELETE FROM projects WHERE id=?", (project_id,))

    def duplicate_project(self, project_id: int) -> int:
        proj = self.get_project(project_id)
        if not proj:
            return -1
        new_id = self.create_project(
            f"{proj.name} (copy)", proj.client, proj.notes, proj.color
        )
        for i, st in enumerate(self.get_subtasks(project_id)):
            self.create_subtask(new_id, st.name, i)
        return new_id

    def get_project_total_seconds(self, project_id: int) -> float:
        row = self._query_one(
            "SELECT COALESCE(SUM(duration_seconds),0) AS total FROM sessions "
            "WHERE project_id=? AND is_running=0",
            (project_id,),
        )
        return row["total"] if row else 0.0

    def get_project_totals(self) -> dict[int, float]:
        """Total tracked seconds for every project, in one query."""
        rows = self._query(
            "SELECT project_id, COALESCE(SUM(duration_seconds),0) AS total "
            "FROM sessions WHERE is_running=0 GROUP BY project_id"
        )
        return {r["project_id"]: r["total"] for r in rows}

    # ── Subtasks ─────────────────────────────────────────────────────────

    def create_subtask(self, project_id: int, name: str, position: int = 0) -> int:
        cur = self._execute(
            "INSERT INTO subtasks(project_id,name,created_at,position) VALUES(?,?,?,?)",
            (project_id, name, iso_utc(now_utc()), position),
        )
        return cur.lastrowid

    def get_subtasks(self, project_id: int) -> list[Subtask]:
        rows = self._query(
            "SELECT * FROM subtasks WHERE project_id=? AND deleted=0 "
            "ORDER BY position,id",
            (project_id,),
        )
        return [Subtask(**dict(r)) for r in rows]

    def update_subtask(self, subtask_id: int, name: str):
        self._execute("UPDATE subtasks SET name=? WHERE id=?", (name, subtask_id))

    def delete_subtask(self, subtask_id: int):
        self._execute("UPDATE subtasks SET deleted=1 WHERE id=?", (subtask_id,))

    def restore_subtask(self, subtask_id: int):
        self._execute("UPDATE subtasks SET deleted=0 WHERE id=?", (subtask_id,))

    def reorder_subtasks(self, subtask_ids: list[int]):
        with self._lock:
            for i, sid in enumerate(subtask_ids):
                self._conn.execute("UPDATE subtasks SET position=? WHERE id=?", (i, sid))
            self._conn.commit()

    def get_subtask_total_seconds(self, subtask_id: int) -> float:
        row = self._query_one(
            "SELECT COALESCE(SUM(duration_seconds),0) AS total FROM sessions "
            "WHERE subtask_id=? AND is_running=0",
            (subtask_id,),
        )
        return row["total"] if row else 0.0

    def get_subtask_totals(self, project_id: int) -> dict[int, float]:
        rows = self._query(
            "SELECT s.subtask_id, COALESCE(SUM(s.duration_seconds),0) AS total "
            "FROM sessions s JOIN subtasks st ON s.subtask_id=st.id "
            "WHERE st.project_id=? AND s.is_running=0 GROUP BY s.subtask_id",
            (project_id,),
        )
        return {r["subtask_id"]: r["total"] for r in rows}

    # ── Sessions ─────────────────────────────────────────────────────────

    def create_session(self, project_id: int, subtask_id: int, started_at) -> int:
        cur = self._execute(
            "INSERT INTO sessions(project_id,subtask_id,started_at,is_running) "
            "VALUES(?,?,?,1)",
            (project_id, subtask_id, iso_utc(started_at)),
        )
        return cur.lastrowid

    def finalize_session(self, session_id: int, ended_at, duration_seconds: float,
                         paused_duration_seconds: float):
        self._execute(
            "UPDATE sessions SET ended_at=?,duration_seconds=?,"
            "paused_duration_seconds=?,is_running=0 WHERE id=?",
            (iso_utc(ended_at), duration_seconds, paused_duration_seconds, session_id),
        )

    def discard_running_sessions(self):
        """Drop zero-length sessions left open by an unclean shutdown."""
        self._execute("DELETE FROM sessions WHERE is_running=1 AND duration_seconds<=0")

    def update_session_notes(self, session_id: int, notes: str):
        self._execute("UPDATE sessions SET notes=? WHERE id=?", (notes, session_id))

    def update_session(self, session_id: int, started_at: str, ended_at: Optional[str],
                       duration_seconds: float, notes: str):
        self._execute(
            "UPDATE sessions SET started_at=?,ended_at=?,duration_seconds=?,notes=? "
            "WHERE id=?",
            (started_at, ended_at, duration_seconds, notes, session_id),
        )

    def get_session(self, session_id: int) -> Optional[Session]:
        row = self._query_one(
            """SELECT s.*, p.name AS project_name, p.client, st.name AS subtask_name
               FROM sessions s
               JOIN projects p  ON s.project_id=p.id
               JOIN subtasks st ON s.subtask_id=st.id
               WHERE s.id=?""",
            (session_id,),
        )
        return Session(**dict(row)) if row else None

    def delete_session(self, session_id: int):
        self._execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def restore_session(self, session: Session):
        self._execute(
            "INSERT INTO sessions(id,subtask_id,project_id,started_at,ended_at,"
            "duration_seconds,paused_duration_seconds,notes,is_running) "
            "VALUES(?,?,?,?,?,?,?,?,0)",
            (session.id, session.subtask_id, session.project_id, session.started_at,
             session.ended_at, session.duration_seconds,
             session.paused_duration_seconds, session.notes),
        )

    @staticmethod
    def _range_clause(date_from: Optional[str], date_to: Optional[str],
                      column: str = "s.started_at"):
        sql, params = "", []
        if date_from:
            sql += f" AND {column} >= ?"
            params.append(date_from)
        if date_to:
            sql += f" AND {column} < ?"
            params.append(date_to)
        return sql, params

    @staticmethod
    def _id_clause(column: str, ids: Optional[Iterable[int]]):
        ids = list(ids) if ids is not None else None
        if ids is None:
            return "", []
        if not ids:
            return " AND 0", []          # explicit empty selection matches nothing
        placeholders = ",".join("?" * len(ids))
        return f" AND {column} IN ({placeholders})", ids

    def get_sessions(self, project_id: Optional[int] = None,
                     project_ids: Optional[Iterable[int]] = None,
                     subtask_id: Optional[int] = None,
                     date_from: Optional[str] = None,
                     date_to: Optional[str] = None,
                     limit: Optional[int] = None) -> list[Session]:
        sql = """
            SELECT s.*, p.name AS project_name, p.client, st.name AS subtask_name
            FROM sessions s
            JOIN projects p  ON s.project_id=p.id
            JOIN subtasks st ON s.subtask_id=st.id
            WHERE s.is_running=0
        """
        params: list[Any] = []
        if project_id:
            sql += " AND s.project_id=?"
            params.append(project_id)
        clause, values = self._id_clause("s.project_id", project_ids)
        sql += clause
        params += values
        if subtask_id:
            sql += " AND s.subtask_id=?"
            params.append(subtask_id)
        clause, values = self._range_clause(date_from, date_to)
        sql += clause
        params += values
        sql += " ORDER BY s.started_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [Session(**dict(r)) for r in self._query(sql, params)]

    def get_sessions_summary(self, date_from: Optional[str] = None,
                             date_to: Optional[str] = None,
                             project_ids: Optional[Iterable[int]] = None) -> list[dict]:
        """One row per project/task pair, with session count and total time."""
        sql = """
            SELECT p.id   AS project_id,
                   p.name AS project_name,
                   p.client,
                   p.color,
                   st.id   AS subtask_id,
                   st.name AS subtask_name,
                   COALESCE(SUM(s.duration_seconds),0) AS total_seconds,
                   COUNT(s.id)                         AS session_count,
                   MIN(s.started_at)                   AS first_session,
                   MAX(s.started_at)                   AS last_session
            FROM sessions s
            JOIN projects p  ON s.project_id=p.id
            JOIN subtasks st ON s.subtask_id=st.id
            WHERE s.is_running=0
        """
        params: list[Any] = []
        clause, values = self._range_clause(date_from, date_to)
        sql += clause
        params += values
        clause, values = self._id_clause("p.id", project_ids)
        sql += clause
        params += values
        sql += (" GROUP BY p.id, st.id "
                "ORDER BY p.name COLLATE NOCASE, st.position, st.name COLLATE NOCASE")
        return [dict(r) for r in self._query(sql, params)]

    def get_daily_totals(self, date_from: Optional[str] = None,
                         date_to: Optional[str] = None,
                         project_ids: Optional[Iterable[int]] = None) -> list[dict]:
        """Total seconds per calendar day — the raw material for the trend bar."""
        sql = """
            SELECT s.started_at, s.duration_seconds
            FROM sessions s
            WHERE s.is_running=0
        """
        params: list[Any] = []
        clause, values = self._range_clause(date_from, date_to)
        sql += clause
        params += values
        clause, values = self._id_clause("s.project_id", project_ids)
        sql += clause
        params += values

        buckets: dict[str, float] = {}
        for row in self._query(sql, params):
            started = parse_utc(row["started_at"])
            if started is None:
                continue
            key = to_local(started).strftime("%Y-%m-%d")
            buckets[key] = buckets.get(key, 0.0) + (row["duration_seconds"] or 0.0)
        return [{"day": k, "total_seconds": v} for k, v in sorted(buckets.items())]

    # ── Timer state ───────────────────────────────────────────────────────

    def save_timer_state(self, state: TimerStateRecord):
        self._execute(
            """
            INSERT INTO timer_state(id,session_id,status,project_id,subtask_id,
                started_at,paused_at,paused_duration_seconds,updated_at)
            VALUES(1,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                session_id=excluded.session_id,
                status=excluded.status,
                project_id=excluded.project_id,
                subtask_id=excluded.subtask_id,
                started_at=excluded.started_at,
                paused_at=excluded.paused_at,
                paused_duration_seconds=excluded.paused_duration_seconds,
                updated_at=excluded.updated_at
            """,
            (state.session_id, state.status, state.project_id, state.subtask_id,
             state.started_at, state.paused_at, state.paused_duration_seconds,
             iso_utc(now_utc())),
        )

    def get_timer_state(self) -> Optional[TimerStateRecord]:
        row = self._query_one("SELECT * FROM timer_state WHERE id=1")
        if not row:
            return None
        return TimerStateRecord(
            session_id=row["session_id"],
            status=row["status"] or "stopped",
            project_id=row["project_id"],
            subtask_id=row["subtask_id"],
            started_at=row["started_at"],
            paused_at=row["paused_at"],
            paused_duration_seconds=row["paused_duration_seconds"] or 0.0,
            updated_at=row["updated_at"],
        )

    def clear_timer_state(self):
        self._execute(
            """
            INSERT INTO timer_state(id,session_id,status,project_id,subtask_id,
                started_at,paused_at,paused_duration_seconds,updated_at)
            VALUES(1,NULL,'stopped',NULL,NULL,NULL,NULL,0,?)
            ON CONFLICT(id) DO UPDATE SET
                session_id=NULL,status='stopped',project_id=NULL,subtask_id=NULL,
                started_at=NULL,paused_at=NULL,paused_duration_seconds=0,
                updated_at=excluded.updated_at
            """,
            (iso_utc(now_utc()),),
        )

    # ── Export history ────────────────────────────────────────────────────

    def save_export(self, export_type: str, filename: str, project_ids: list[int],
                    date_from: str, date_to: str):
        self._execute(
            "INSERT INTO export_history(export_type,filename,project_ids,"
            "date_from,date_to,created_at) VALUES(?,?,?,?,?,?)",
            (export_type, filename, json.dumps(project_ids), date_from, date_to,
             iso_utc(now_utc())),
        )

    def get_export_history(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._query(
            "SELECT * FROM export_history ORDER BY created_at DESC LIMIT ?", (limit,)
        )]

    # ── Search ────────────────────────────────────────────────────────────

    def search(self, query: str) -> dict:
        like = f"%{query}%"
        projects = self._query(
            "SELECT * FROM projects WHERE (name LIKE ? OR client LIKE ? OR notes LIKE ?) "
            "AND archived=0",
            (like, like, like),
        )
        subtasks = self._query(
            """SELECT st.*, p.name AS project_name FROM subtasks st
               JOIN projects p ON st.project_id=p.id
               WHERE st.name LIKE ? AND st.deleted=0""",
            (like,),
        )
        return {
            "projects": [dict(r) for r in projects],
            "subtasks": [dict(r) for r in subtasks],
        }

    # ── Whole-database export ─────────────────────────────────────────────

    def export_all(self) -> dict:
        with self._lock:
            return {
                "schema_version": SCHEMA_VERSION,
                "exported_at": iso_utc(now_utc()),
                "projects": [dict(r) for r in self._conn.execute(
                    "SELECT * FROM projects").fetchall()],
                "subtasks": [dict(r) for r in self._conn.execute(
                    "SELECT * FROM subtasks").fetchall()],
                "sessions": [dict(r) for r in self._conn.execute(
                    "SELECT * FROM sessions WHERE is_running=0").fetchall()],
                "settings": [dict(r) for r in self._conn.execute(
                    "SELECT * FROM settings").fetchall()],
            }

import sqlite3
import threading
from datetime import timedelta

from src.database import SCHEMA_VERSION, Database
from src.timeutil import iso_utc, now_utc


def test_schema_version_is_recorded(db):
    row = db._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    assert row["v"] == SCHEMA_VERSION


def test_project_round_trip(db):
    project_id = db.create_project("Aqueduct", "Fulcrum", "notes", "#FF0000")
    project = db.get_project(project_id)
    assert (project.name, project.client, project.color) == (
        "Aqueduct", "Fulcrum", "#FF0000")


def test_archived_projects_are_hidden_but_not_lost(db):
    project_id = db.create_project("Old work")
    db.archive_project(project_id, True)
    assert db.get_projects() == []
    assert [p.id for p in db.get_projects(include_archived=True)] == [project_id]


def test_summary_totals_match_sessions(db, sample):
    rows = db.get_sessions_summary()
    assert {r["subtask_name"] for r in rows} == {"Survey", "Modelling"}
    assert sum(r["total_seconds"] for r in rows) == 6.5 * 3600
    survey = next(r for r in rows if r["subtask_name"] == "Survey")
    assert survey["session_count"] == 2
    assert survey["total_seconds"] == 3.5 * 3600


def test_summary_can_be_limited_to_projects(db, sample):
    other = db.create_project("Unrelated")
    assert db.get_sessions_summary(project_ids=[other]) == []
    assert db.get_sessions_summary(project_ids=[sample["project_id"]])


def test_empty_project_selection_matches_nothing(db, sample):
    """An explicit empty selection must not silently mean 'everything'."""
    assert db.get_sessions_summary(project_ids=[]) == []
    assert db.get_sessions(project_ids=[]) == []


def test_project_ids_of_none_means_no_filter(db, sample):
    assert db.get_sessions_summary(project_ids=None)


def test_date_range_is_half_open(db):
    project_id = db.create_project("P")
    task_id = db.create_subtask(project_id, "T")
    boundary = now_utc().replace(hour=12, minute=0, second=0)
    db._conn.execute(
        "INSERT INTO sessions(project_id,subtask_id,started_at,duration_seconds,"
        "is_running) VALUES(?,?,?,?,0)",
        (project_id, task_id, iso_utc(boundary), 60),
    )
    db.commit()

    assert db.get_sessions(date_from=iso_utc(boundary))
    assert db.get_sessions(date_to=iso_utc(boundary)) == []
    assert db.get_sessions(date_to=iso_utc(boundary + timedelta(seconds=1)))


def test_running_sessions_are_excluded_from_reports(db):
    project_id = db.create_project("P")
    task_id = db.create_subtask(project_id, "T")
    db.create_session(project_id, task_id, now_utc())
    assert db.get_sessions() == []
    assert db.get_sessions_summary() == []


def test_deleting_a_session_can_be_undone(db, sample):
    session = db.get_sessions()[0]
    db.delete_session(session.id)
    assert all(s.id != session.id for s in db.get_sessions())
    db.restore_session(session)
    restored = db.get_session(session.id)
    assert restored.duration_seconds == session.duration_seconds


def test_project_totals_batch_matches_individual(db, sample):
    totals = db.get_project_totals()
    assert totals[sample["project_id"]] == db.get_project_total_seconds(
        sample["project_id"])


def test_daily_totals_bucket_by_local_day(db, sample):
    days = db.get_daily_totals()
    assert days
    assert sum(d["total_seconds"] for d in days) == 6.5 * 3600


def test_settings_upsert(db):
    db.set_setting("worker_name", "Elliott")
    db.set_setting("worker_name", "Elliott Cromer")
    assert db.get_setting("worker_name") == "Elliott Cromer"
    assert db.get_setting("never_set", "fallback") == "fallback"


def test_migration_normalises_legacy_naive_timestamps(tmp_path):
    """A database written before offsets were stored must still filter correctly."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            client TEXT DEFAULT '', notes TEXT DEFAULT '', color TEXT DEFAULT '',
            created_at TEXT NOT NULL, archived INTEGER DEFAULT 0);
        CREATE TABLE subtasks (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER,
            name TEXT NOT NULL, created_at TEXT NOT NULL, position INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0);
        CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, subtask_id INTEGER,
            project_id INTEGER, started_at TEXT NOT NULL, ended_at TEXT,
            duration_seconds REAL DEFAULT 0, paused_duration_seconds REAL DEFAULT 0,
            notes TEXT DEFAULT '', is_running INTEGER DEFAULT 0);
        INSERT INTO projects(id,name,created_at) VALUES(1,'Legacy','2026-06-01T08:00:00');
        INSERT INTO subtasks(id,project_id,name,created_at)
            VALUES(1,1,'Task','2026-06-01T08:00:00');
        INSERT INTO sessions(id,subtask_id,project_id,started_at,ended_at,
            duration_seconds,is_running)
            VALUES(1,1,1,'2026-06-01T09:00:00','2026-06-01T11:00:00',7200,0);
    """)
    conn.commit()
    conn.close()

    database = Database(path)
    try:
        stored = database._conn.execute(
            "SELECT started_at, ended_at FROM sessions WHERE id=1").fetchone()
        assert stored["started_at"] == "2026-06-01T09:00:00+00:00"
        assert stored["ended_at"] == "2026-06-01T11:00:00+00:00"
        # The instant is unchanged — only the encoding was made explicit.
        assert database.get_sessions()[0].duration_seconds == 7200
    finally:
        database.close()


def test_migration_backs_up_before_rewriting(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, "
                 "client TEXT, notes TEXT, color TEXT, created_at TEXT, "
                 "archived INTEGER)")
    conn.execute("INSERT INTO projects VALUES(1,'P','','','','2026-06-01T08:00:00',0)")
    conn.commit()
    conn.close()

    database = Database(path)
    database.close()
    assert list(tmp_path.glob("*pre-v*-migration.db"))


def test_concurrent_writes_do_not_corrupt(db):
    """The shared connection is guarded by a lock, so threads cannot interleave."""
    def worker(offset):
        for i in range(25):
            db.create_project(f"P{offset}-{i}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(db.get_projects()) == 100


def test_fresh_database_makes_no_migration_snapshot(tmp_path):
    """A new install should not litter the data folder with an empty snapshot."""
    database = Database(tmp_path / "fresh.db")
    database.close()
    assert not list(tmp_path.glob("*pre-v*-migration.db"))


def test_history_search_matches_all_fields_and_literal_wildcards(db, sample):
    session = db.get_sessions()[0]
    db.update_session_notes(session.id, "Reviewed 100% of drawings_with_notes")
    for query in ("aqueduct", "FULCRUM", "100%", "drawings_with_notes"):
        assert db.get_sessions(search=query)
    assert len(db.get_sessions(search="Survey")) == 2
    assert len(db.get_sessions(search="%")) == 1
    assert db.get_sessions(search="missing") == []
    assert db.get_sessions(search="aqueduct", project_ids=[]) == []


def test_history_paging_has_no_duplicates_when_timestamps_match(db, sample):
    for session in db.get_sessions():
        db.update_session(session.id, "2026-01-01T00:00:00+00:00", None, 60, "")
    first = db.get_sessions(limit=2)
    second = db.get_sessions(limit=2, offset=2)
    assert len(first) == 2 and len(second) == 1
    assert len({s.id for s in first + second}) == 3
    assert [s.id for s in first + second] == sorted([s.id for s in first + second], reverse=True)

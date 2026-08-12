import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def sample(db):
    """A project with two tasks and three finished sessions."""
    from datetime import timedelta

    from src.timeutil import iso_utc, now_utc

    project_id = db.create_project("Aqueduct", "Fulcrum", "notes", "#4DABF7")
    first = db.create_subtask(project_id, "Survey", 0)
    second = db.create_subtask(project_id, "Modelling", 1)

    start = now_utc() - timedelta(days=1)
    for task_id, hours in ((first, 2.0), (first, 1.5), (second, 3.0)):
        db._conn.execute(
            "INSERT INTO sessions(project_id,subtask_id,started_at,ended_at,"
            "duration_seconds,is_running) VALUES(?,?,?,?,?,0)",
            (project_id, task_id, iso_utc(start),
             iso_utc(start + timedelta(hours=hours)), hours * 3600),
        )
        start += timedelta(hours=hours + 1)
    db.commit()
    return {"project_id": project_id, "tasks": (first, second)}

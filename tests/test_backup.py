import json
import sqlite3

import pytest

from src.backup import BackupManager
from src.timer_engine import TimerEngine


@pytest.mark.parametrize("payload", [{}, [], None, {"projects": []},
                                      {"schema_version": 999}])
def test_unrelated_json_cannot_erase_data(db, sample, tmp_path, payload):
    backup = BackupManager(db, tmp_path)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = db.export_all()
    assert not backup.restore_backup(path)
    assert db.get_sessions()
    assert db.export_all()["projects"] == before["projects"]


def test_snapshot_includes_wal_and_can_be_opened_alone(db, sample, tmp_path):
    backup = BackupManager(db, tmp_path)
    path = backup.snapshot_database("test")
    with sqlite3.connect(path) as copy:
        assert copy.execute("SELECT count(*) FROM sessions").fetchone()[0] == 3
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restore_replaces_state_and_retains_safety_copy(db, sample, tmp_path):
    backup = BackupManager(db, tmp_path)
    path = backup.make_backup("manual")
    timer = TimerEngine(db)
    timer.start(sample["project_id"], sample["tasks"][0])
    db.set_setting("extra", "stale")
    db.save_export("CSV", "old.csv", [], "", "")
    assert backup.restore_backup(path)
    assert len(db.get_sessions()) == 3
    assert db.get_timer_state() is None
    assert db.get_setting("extra", "missing") == "missing"
    assert db.get_export_history() == []
    assert list(backup.backup_dir.glob("*pre-restore*.db"))


def test_failed_snapshot_aborts_restore(db, sample, tmp_path, monkeypatch):
    backup = BackupManager(db, tmp_path)
    path = backup.make_backup()
    db.create_project("Keep me")
    monkeypatch.setattr(backup, "snapshot_database", lambda _label: None)
    assert not backup.restore_backup(path)
    assert len(db.get_projects()) == 2


def test_invalid_foreign_key_rolls_back_every_table(db, sample, tmp_path):
    backup = BackupManager(db, tmp_path)
    data = db.export_all()
    data["subtasks"][0]["project_id"] = 99999
    data["sessions"] = []
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not backup.restore_backup(path)
    assert len(db.get_sessions()) == 3
    assert db.get_project(sample["project_id"]).name == "Aqueduct"


def test_retention_keeps_manual_and_newest_auto(db, tmp_path):
    backup = BackupManager(db, tmp_path)
    backup.MAX_BACKUPS = 2
    manual = backup.make_backup("manual")
    automatic = [backup.make_backup() for _ in range(4)]
    assert manual.exists()
    assert all(p.exists() for p in automatic[-2:])
    assert not any(p.exists() for p in automatic[:2])
    assert len(set(automatic)) == 4
    assert not list(backup.backup_dir.glob("*.tmp"))

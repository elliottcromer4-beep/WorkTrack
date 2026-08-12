#!/usr/bin/env python3
"""
Fill WorkTrack with example data so the app can be seen working.

    python seed_data.py            # writes to the real data directory
    python seed_data.py --dry-run  # writes to a throwaway directory instead

This replaces any projects, tasks and sessions already recorded, so it asks
first unless --yes is given.
"""
import argparse
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.database import Database  # noqa: E402
from src.system import data_dir  # noqa: E402
from src.timeutil import iso_utc, now_local  # noqa: E402
from src.version import APP_NAME  # noqa: E402

PROJECTS = [
    {
        "name": "Snowy Hydro Aqueduct",
        "client": "Fulcrum Engineering",
        "notes": "Surveying and modelling the main aqueduct corridor.",
        "color": "#4DABF7",
        "tasks": [
            ("Download data", [(0, 9, 0.75, "FTP pull from the Fulcrum server")]),
            ("Clean and process data", [(1, 9, 3.75, "LAS filtering, grid alignment"),
                                        (6, 13, 2.25, "")]),
            ("Generate DEM", [(2, 10, 0.5, "")]),
            ("QA and review", [(3, 15, 1.0, "Final checks with the PM")]),
        ],
    },
    {
        "name": "Batlow Road Corridor Extension",
        "client": "Department of Transport",
        "notes": "Road corridor survey, stage 3.",
        "color": "#69DB7C",
        "tasks": [
            ("Site reconnaissance", [(2, 8, 2.0, "")]),
            ("Field data collection", [(4, 7, 6.5, "Full day on site")]),
            ("Point cloud processing", [(5, 9, 4.0, "")]),
            ("Report writing", [(7, 10, 2.5, "")]),
        ],
    },
    {
        "name": "Murray Hydrology Model",
        "client": "CSIRO",
        "notes": "Catchment runoff modelling.",
        "color": "#CC5DE8",
        "tasks": [
            ("Data integration", [(1, 13, 3.0, "")]),
            ("Model calibration", [(3, 11, 4.5, "")]),
            ("Scenario runs", [(8, 9, 2.75, "Ten scenarios queued overnight")]),
        ],
    },
]


def seed(db: Database, worker: str = "Elliott Cromer"):
    db._conn.execute("DELETE FROM sessions")
    db._conn.execute("DELETE FROM subtasks")
    db._conn.execute("DELETE FROM projects")
    db.commit()
    db.set_setting("worker_name", worker)

    # Anchor the example data to the recent past so date filters have something
    # to find whenever this is run.
    base = (now_local() - timedelta(days=9)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    sessions = 0
    for spec in PROJECTS:
        project_id = db.create_project(spec["name"], spec["client"], spec["notes"],
                                       spec["color"])
        for position, (task_name, entries) in enumerate(spec["tasks"]):
            task_id = db.create_subtask(project_id, task_name, position)
            for day, hour, hours, note in entries:
                start = base + timedelta(days=day, hours=hour)
                end = start + timedelta(hours=hours)
                db._conn.execute(
                    "INSERT INTO sessions(project_id,subtask_id,started_at,ended_at,"
                    "duration_seconds,notes,is_running) VALUES(?,?,?,?,?,?,0)",
                    (project_id, task_id, iso_utc(start), iso_utc(end),
                     hours * 3600, note),
                )
                sessions += 1
    db.commit()
    return sessions


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Seed {APP_NAME} with example data")
    parser.add_argument("--dry-run", action="store_true",
                        help="write to a temporary directory instead of the real one")
    parser.add_argument("--yes", action="store_true", help="do not ask before replacing")
    args = parser.parse_args()

    directory = (Path(tempfile.mkdtemp(prefix="worktrack_seed_"))
                 if args.dry_run else data_dir(APP_NAME))

    if not args.dry_run and not args.yes:
        answer = input(f"This replaces all data in {directory}. Continue? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Cancelled.")
            return 1

    db = Database(directory / "worktrack.db")
    count = seed(db)
    db.close()

    print(f"Seeded {len(PROJECTS)} projects and {count} sessions.")
    print(f"  Data directory: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# WorkTrack

Desktop time tracking for professional services: a floating timer that stays
out of the way, and a timesheet you can send to a client without editing it
first.

Windows, macOS and Linux. Python 3.9+. Everything is stored locally in SQLite —
no account, no server, no telemetry.

---

## What it does

**A floating widget** sits above your work with the running clock and
start / pause / stop. Drag it anywhere; it remembers where you put it, and
falls back to a sensible corner if that monitor is gone.

**A dashboard** behind it holds the detail:

| Panel | What it is for |
| --- | --- |
| Projects | Projects, clients and their tasks; start a timer on any task |
| History | Every recorded session — edit, delete, or add one you forgot |
| Reports | Where the time went, by project and task, for any period |
| Exports | PDF timesheet, Excel workbook or CSV |
| Settings | Your name for reports, backups, restore, data folder |

**A timesheet report** in PDF, laid out to be sent on: a summary page with
totals and each project's share, then the detail per project, with a full
session log if you want the evidence attached. Hours appear both as `H:MM` and
as decimal hours, because invoices want the second one.

Excel exports write hours as real numbers, so the recipient can pivot and total
them without retyping anything.

---

## Install

```bash
pip install -r requirements.txt
```

Then:

```bash
python main.py
```

On Windows, `install.bat` and `run.bat` do the same thing by double-click.

To try it with example data first:

```bash
python seed_data.py --dry-run
```

`--dry-run` writes to a temporary folder. Without it, seeding **replaces**
everything already recorded, and asks before doing so.

---

## Building a standalone executable

```bash
pip install pyinstaller
pyinstaller WorkTrack.spec
```

The result is `dist/WorkTrack.exe` — a single windowed executable carrying its
icon and Windows version metadata, both read from `src/version.py`.

`build.bat` runs the same thing on Windows.

---

## How your data is stored

Everything lives in one SQLite database under your user profile:

| Platform | Location |
| --- | --- |
| Windows | `%APPDATA%\WorkTrack\` |
| macOS | `~/Library/Application Support/WorkTrack/` |
| Linux | `~/.local/share/WorkTrack/` |

Settings → **Data folder → Open** takes you there. Alongside the database:

- `backups/` — a JSON snapshot on the first launch of each day, plus any you
  take by hand. The last 30 are kept. JSON rather than a database copy so the
  data stays readable and importable even without this app.
- `exports/` — the default folder offered when you save a report.

Pass `--data-dir` to keep everything somewhere else — a USB stick, for
instance, for a portable install:

```bash
python main.py --data-dir D:/WorkTrack
```

### Time zones

Times are **stored in UTC and displayed in your local time zone**. That is what
keeps a duration correct across a daylight-saving change or a trip, while
"today" still means your today rather than UTC's.

### If the app is killed while a timer is running

A running timer records a heartbeat every 30 seconds. On the next launch:

- if the heartbeat is recent, the timer picks up where it was;
- if it is stale — the machine was shut down, or the app was killed — the
  session is closed off **at the last heartbeat**, and you are told. A laptop
  closed on Friday will not bill you for the weekend.

---

## Keyboard

| Key | Action |
| --- | --- |
| `Ctrl` + `S` | Save (visible confirmation; SQLite has already committed) |
| `Ctrl` + `Z` | Undo the last delete or archive |
| `Ctrl` + `F` | Jump to search |
| `Enter` | Save the open dialog, or add the task you have typed |
| `Escape` | Close the open dialog |

Double-click a project to edit it, or a task name to rename it in place.

---

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite covers duration and time-zone handling, the database and its
migration, the timer state machine, and every export. The PDF tests are worth
knowing about: they rasterise the generated report and assert that **no two
text spans overlap** and that nothing crosses the page margins. That is the
regression net for the layout faults this report used to have — a heading
printed through the line beneath it, and long task names running over the next
column.

There is also a smoke suite that builds the real windows and steps through
every panel, which catches the wiring mistakes unit tests cannot see.

```
src/
  version.py       application identity, single source of the version number
  timeutil.py      UTC storage, local display, date parsing
  models.py        records and duration formatting
  database.py      SQLite access, schema migrations, thread-safe
  timer_engine.py  the timer state machine
  backup.py        JSON snapshots, restore
  system.py        platform helpers: data folder, opening files, single instance
  exports/         pdf_exp · xlsx_exp · csv_exp · common
  ui/              app (controller) · widget · dashboard · theme
```

---

## Licence

MIT — see [LICENSE](LICENSE).

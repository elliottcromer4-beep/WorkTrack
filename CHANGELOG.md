# Changelog

All notable changes to WorkTrack are recorded here.
This project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-08-13

First release considered fit to hand a timesheet to a client from.

### The PDF timesheet, rebuilt

- **Fixed overlapping text.** The report title was drawn at 22pt against an
  inherited 14pt leading, so "WorkTrack" and "Time Report" printed through each
  other. Every style now derives its leading from its own font size.
- **Fixed text running over the next column.** Table cells held raw strings,
  which ReportLab does not wrap, so a long task name ran straight through the
  Sessions column. Every variable-length cell is now a wrapping paragraph, and
  column widths are asserted to sum to the frame width.
- **Fixed names being silently mangled.** A project called `R&D <Prototype>`
  printed as `R&D;` — the unescaped angle brackets were parsed as markup and
  discarded. All text is now escaped.
- **Fixed page breaks.** Split tables repeat their column headers, and a
  project heading can no longer be stranded at the foot of a page.
- **New layout.** A cover block, a row of totals, a summary table with each
  project's share of the time, then per-project detail with an optional session
  log. Running header and footer with "Page N of M" on every page.
- Durations are shown as `H:MM` *and* as decimal hours, for invoicing.
- The Notes column is dropped when no session has a note, rather than reserving
  a quarter of the page width for nothing.

### Correctness

- **Time zones.** Timestamps are stored in UTC with an explicit offset and
  displayed in local time. Previously naive UTC values were shown as if they
  were local, so recorded times were wrong by the size of your offset, and
  "Today" in History meant UTC's today. A migration converts existing data
  losslessly, taking a copy of the database first.
- **Abandoned timers no longer inflate a timesheet.** A running timer records a
  heartbeat; if the app is killed and reopened much later, the session is
  closed off at the last heartbeat instead of counting the intervening days.
- **The database is thread-safe.** The connection is shared with the backup
  thread and is now guarded by a lock.
- **Date fields are validated.** An unparseable date was silently ignored,
  producing a report over the wrong period. The "to" date is now inclusive.
- Archived projects are included in exports, so their time stops vanishing.
- Excel sheet names are made valid and unique instead of corrupting the file.
- `format_duration_human` no longer prints "30m 0s".
- Removed the deprecated `datetime.utcnow()` throughout.

### Interface

- Confirmation before deleting a task or session, or archiving a project — each
  undoable with `Ctrl` + `Z`.
- Exports ask where to save, rather than writing somewhere out of sight and
  opening Explorer unprompted.
- **Fixed the report bars**, which were measured before layout had run and so
  always came out at the minimum width regardless of their share.
- Add a time entry by hand, for the session you forgot to start.
- Restore from a backup, from Settings.
- Quick date ranges: this week, last week, this month, last month.
- `Ctrl` + `F` now actually focuses the search box.
- Removed the global hotkey setting, which had no implementation behind it.

### Packaging

- A second copy of the app can no longer be launched over the first.
- A crash in a windowed build writes `crash.log` and says so, instead of
  vanishing.
- `--data-dir` for a portable install; `--version`.
- The executable carries proper Windows version metadata, generated from
  `src/version.py`.
- Cross-platform file opening — `os.startfile` was called unguarded.
- 108 tests, including PDF layout regression tests that rasterise the report and
  assert no text overlaps or crosses the margins, and a smoke suite that builds
  the real windows.
- Added `pyproject.toml`, `README.md`, `LICENSE`, `.gitignore` and CI.

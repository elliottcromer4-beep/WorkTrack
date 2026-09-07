"""
Export tests.

The PDF tests are the regression net for the layout faults this report used to
have: a heading printed through the line below it, task names running over the
next column, and ``&``/``<`` in a project name being swallowed by the markup
parser.
"""
import csv
from datetime import timedelta

import pytest

from src.exports.common import default_filename, period_label
from src.exports.csv_exp import export_csv
from src.exports.pdf_exp import export_pdf
from src.exports.xlsx_exp import export_xlsx
from src.timeutil import iso_utc, now_utc

fitz = pytest.importorskip("fitz", reason="PyMuPDF is needed to inspect the PDF")

HOSTILE_NAME = 'R&D <Prototype> "Alpha"'
LONG_TASK = ("Field data collection — RTK GNSS control network establishment, "
             "validation and adjustment against the state control mark network")


@pytest.fixture
def report_db(db):
    """A dataset chosen to break a careless layout."""
    start = now_utc() - timedelta(days=3)

    def add(project_id, task_id, hours, notes=""):
        nonlocal start
        db._conn.execute(
            "INSERT INTO sessions(project_id,subtask_id,started_at,ended_at,"
            "duration_seconds,notes,is_running) VALUES(?,?,?,?,?,?,0)",
            (project_id, task_id, iso_utc(start),
             iso_utc(start + timedelta(hours=hours)), hours * 3600, notes),
        )
        start += timedelta(hours=hours + 1)

    ordinary = db.create_project("Snowy Hydro Aqueduct", "Fulcrum Engineering",
                                 "Corridor survey.", "#4DABF7")
    add(ordinary, db.create_subtask(ordinary, "Download data", 0), 0.75, "FTP pull")
    add(ordinary, db.create_subtask(ordinary, LONG_TASK, 1), 5.5)

    hostile = db.create_project(HOSTILE_NAME, "Smith & Co.", "", "#CC5DE8")
    add(hostile, db.create_subtask(hostile, "Cost < budget & schedule > plan", 0), 3.0)

    # Enough rows to force the task table across a page boundary.
    long_project = db.create_project("Murray Hydrology Model", "CSIRO", "", "#FF922B")
    for index in range(34):
        task_id = db.create_subtask(long_project,
                                    f"Scenario run {index + 1:02d} — sweep", index)
        add(long_project, task_id, 0.5)

    db.commit()
    return db


# ── Helpers ──────────────────────────────────────────────────────────────────

def _spans(page):
    """Every drawn text span with its bounding box."""
    found = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = "".join(char["c"] for char in span.get("chars", []))
                if text.strip():
                    found.append((text, fitz.Rect(span["bbox"])))
    return found


def _overlapping_pairs(page, tolerance=0.45):
    """
    Pairs of text spans that visibly sit on top of each other.

    Neighbouring spans on a line share an edge, so an intersection only counts
    when it covers a real fraction of the smaller span's area.
    """
    spans = _spans(page)
    clashes = []
    for i, (text_a, box_a) in enumerate(spans):
        for text_b, box_b in spans[i + 1:]:
            overlap = box_a & box_b
            if overlap.is_empty:
                continue
            area = overlap.width * overlap.height
            smaller = min(box_a.width * box_a.height, box_b.width * box_b.height)
            if smaller > 0 and area / smaller > tolerance:
                clashes.append((text_a, text_b))
    return clashes


# ── PDF ──────────────────────────────────────────────────────────────────────

def test_pdf_is_written_and_readable(report_db, tmp_path):
    target = tmp_path / "report.pdf"
    export_pdf(report_db, report_db.get_projects(), None, None, "Elliott Cromer",
               str(target))
    assert target.exists() and target.stat().st_size > 1000
    with fitz.open(target) as doc:
        assert doc.page_count >= 2


def test_pdf_has_no_overlapping_text(report_db, tmp_path):
    """The original report drew a 22pt title on a 14pt line."""
    target = tmp_path / "report.pdf"
    export_pdf(report_db, report_db.get_projects(), None, None, "Elliott Cromer",
               str(target))
    with fitz.open(target) as doc:
        for number, page in enumerate(doc, 1):
            clashes = _overlapping_pairs(page)
            assert not clashes, f"page {number} has overlapping text: {clashes[:3]}"


def test_pdf_keeps_text_inside_the_page_margins(report_db, tmp_path):
    target = tmp_path / "report.pdf"
    export_pdf(report_db, report_db.get_projects(), None, None, "Elliott Cromer",
               str(target))
    with fitz.open(target) as doc:
        for number, page in enumerate(doc, 1):
            width = page.rect.width
            for text, box in _spans(page):
                assert box.x0 >= 30, f"page {number}: {text!r} runs off the left"
                assert box.x1 <= width - 30, f"page {number}: {text!r} runs off the right"


def test_pdf_preserves_markup_hostile_names(report_db, tmp_path):
    """``R&D <Prototype>`` used to print as ``R&D;`` — the tag was eaten."""
    target = tmp_path / "report.pdf"
    export_pdf(report_db, report_db.get_projects(), None, None, "Elliott Cromer",
               str(target))
    with fitz.open(target) as doc:
        text = "".join(page.get_text() for page in doc)
    assert HOSTILE_NAME in text
    assert "Cost < budget & schedule > plan" in text


def test_pdf_numbers_every_page(report_db, tmp_path):
    target = tmp_path / "report.pdf"
    export_pdf(report_db, report_db.get_projects(), None, None, "Elliott Cromer",
               str(target))
    with fitz.open(target) as doc:
        total = doc.page_count
        for number, page in enumerate(doc, 1):
            assert f"Page {number} of {total}" in page.get_text()


def test_pdf_repeats_table_headers_after_a_split(report_db, tmp_path):
    """A continued table must carry its column headings onto the next page."""
    target = tmp_path / "report.pdf"
    export_pdf(report_db, report_db.get_projects(), None, None, "Elliott Cromer",
               str(target), include_sessions=False)
    with fitz.open(target) as doc:
        pages_with_rows = [
            page.get_text() for page in doc if "Scenario run" in page.get_text()
        ]
    assert len(pages_with_rows) > 1, "the long project should span pages"
    for text in pages_with_rows:
        assert "Task" in text and "Hours (decimal)" in text


def test_pdf_totals_match_the_recorded_time(report_db, tmp_path):
    target = tmp_path / "report.pdf"
    export_pdf(report_db, report_db.get_projects(), None, None, "Elliott Cromer",
               str(target))
    expected = sum(r["total_seconds"] for r in report_db.get_sessions_summary())
    with fitz.open(target) as doc:
        text = doc[0].get_text()
    assert f"{expected / 3600:.2f}" in text


def test_pdf_handles_an_empty_period(db, tmp_path):
    """No data is a normal outcome, not a crash."""
    db.create_project("Nothing recorded")
    target = tmp_path / "empty.pdf"
    export_pdf(db, db.get_projects(), None, None, "", str(target))
    with fitz.open(target) as doc:
        assert "No time was recorded" in doc[0].get_text()


def test_pdf_omits_the_notes_column_when_there_are_no_notes(db, tmp_path):
    project_id = db.create_project("Quiet project")
    task_id = db.create_subtask(project_id, "Task")
    start = now_utc()
    db._conn.execute(
        "INSERT INTO sessions(project_id,subtask_id,started_at,ended_at,"
        "duration_seconds,notes,is_running) VALUES(?,?,?,?,?,'',0)",
        (project_id, task_id, iso_utc(start), iso_utc(start + timedelta(hours=1)),
         3600),
    )
    db.commit()

    target = tmp_path / "quiet.pdf"
    export_pdf(db, db.get_projects(), None, None, "", str(target))
    with fitz.open(target) as doc:
        assert "Notes" not in "".join(page.get_text() for page in doc)


# ── CSV ──────────────────────────────────────────────────────────────────────

def test_csv_has_one_row_per_session(report_db, tmp_path):
    target = tmp_path / "sessions.csv"
    project_ids = [p.id for p in report_db.get_projects()]
    export_csv(report_db, project_ids, None, None, str(target))

    with open(target, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == len(report_db.get_sessions())
    assert rows[0]["Duration (hours)"]
    assert HOSTILE_NAME in {row["Project"] for row in rows}


def test_csv_respects_the_project_filter(report_db, tmp_path):
    target = tmp_path / "one.csv"
    chosen = next(p for p in report_db.get_projects() if p.name == HOSTILE_NAME)
    export_csv(report_db, [chosen.id], None, None, str(target))

    with open(target, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["Project"] for row in rows} == {HOSTILE_NAME}


# ── Excel ────────────────────────────────────────────────────────────────────

def test_xlsx_is_written_with_a_sheet_per_project(report_db, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    target = tmp_path / "report.xlsx"
    export_xlsx(report_db, report_db.get_projects(), None, None, "Elliott Cromer",
                str(target))

    workbook = openpyxl.load_workbook(target)
    assert workbook.sheetnames[0] == "Summary"
    assert len(workbook.sheetnames) == 1 + len(report_db.get_projects())
    for name in workbook.sheetnames:
        assert len(name) <= 31
        assert not set(name) & set(r"\/*?:[]")


def test_xlsx_writes_hours_as_numbers(report_db, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    target = tmp_path / "report.xlsx"
    export_xlsx(report_db, report_db.get_projects(), None, None, "", str(target))

    sheet = openpyxl.load_workbook(target)["Summary"]
    hours = [cell.value for row in sheet.iter_rows(min_col=6, max_col=6)
             for cell in row if isinstance(cell.value, (int, float))]
    assert hours, "the Hours column should contain numbers, not text"


def test_xlsx_sheet_names_stay_unique(db, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    start = now_utc()
    for suffix in ("A", "B"):
        # Two long names that collide once truncated to Excel's 31-character cap.
        project_id = db.create_project(
            f"Very long project name that Excel will truncate {suffix}")
        task_id = db.create_subtask(project_id, "Task")
        db._conn.execute(
            "INSERT INTO sessions(project_id,subtask_id,started_at,ended_at,"
            "duration_seconds,is_running) VALUES(?,?,?,?,?,0)",
            (project_id, task_id, iso_utc(start),
             iso_utc(start + timedelta(hours=1)), 3600),
        )
    db.commit()

    target = tmp_path / "collide.xlsx"
    export_xlsx(db, db.get_projects(), None, None, "", str(target))
    names = openpyxl.load_workbook(target).sheetnames
    assert len(names) == len(set(names)) == 3


# ── Shared helpers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("date_from,date_to,expected", [
    (None, None, "All recorded time"),
    ("2026-06-01T00:00:00+00:00", None, "From 01 Jun 2026"),
    (None, "2026-06-30T00:00:00+00:00", "Up to 30 Jun 2026"),
])
def test_period_label(date_from, date_to, expected):
    assert period_label(date_from, date_to) == expected


def test_default_filename_is_timestamped():
    name = default_filename("WorkTrack_Timesheet", "pdf")
    assert name.startswith("WorkTrack_Timesheet_") and name.endswith(".pdf")

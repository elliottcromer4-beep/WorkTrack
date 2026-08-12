"""
Timesheet report as a PDF.

Layout rules that keep the document from ever colliding with itself:

* every piece of variable-length text is a ``Paragraph``, so it wraps inside
  its cell instead of running over the neighbouring column;
* every string is XML-escaped, so a project called ``R&D <Alpha>`` prints as
  written rather than being eaten by the markup parser;
* every style sets its own leading, so a large heading cannot be drawn on top
  of the line beneath it;
* tables declare ``repeatRows`` and section headers are bound to the rows that
  follow them, so a page break never orphans a heading or a column header.

Column widths are declared in millimetres and always sum to the frame width —
that is what guarantees the arithmetic behind the first rule.
"""
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from ..database import Database
from ..models import Project, format_hm, format_hours_decimal
from ..timeutil import fmt_date, fmt_time, now_local, parse_local
from ..version import APP_NAME, __version__
from .common import period_label

# ── Page geometry ────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = A4
MARGIN_X = 18 * mm
MARGIN_TOP = 26 * mm
MARGIN_BOTTOM = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X          # 174 mm

# ── Print palette ────────────────────────────────────────────────────────────
#
# Deliberately not the app's dark-UI palette: those colours are tuned for a
# backlit screen and print washed out.  These are weighted for ink on paper.

INK = colors.HexColor("#12161C")
INK_SOFT = colors.HexColor("#39424F")
MUTED = colors.HexColor("#6B7684")
FAINT = colors.HexColor("#9AA4B2")
RULE = colors.HexColor("#D9DFE7")
RULE_SOFT = colors.HexColor("#ECEFF4")
BAND = colors.HexColor("#0F2A47")
ACCENT = colors.HexColor("#2C7BE5")
ACCENT_SOFT = colors.HexColor("#EAF2FE")
ZEBRA = colors.HexColor("#F7F9FC")
PAPER = colors.white


def _style(name: str, size: float = 9.5, leading_ratio: float = 1.32, **kwargs
           ) -> ParagraphStyle:
    """
    Build a paragraph style with leading derived from the font size.

    Deriving it is the point: the original report set a 22pt title against an
    inherited 14pt leading, which is exactly how a title ends up printed
    through the line below it.
    """
    settings = {
        "name": name,
        "fontName": "Helvetica",
        "fontSize": size,
        "leading": round(size * leading_ratio, 2),
        "textColor": INK,
    }
    settings.update(kwargs)
    return ParagraphStyle(**settings)


S_TITLE = _style("title", 26, 1.15, fontName="Helvetica-Bold", textColor=INK)
S_SUBTITLE = _style("subtitle", 11.5, 1.35, textColor=MUTED)
S_SECTION = _style("section", 12, 1.3, fontName="Helvetica-Bold", textColor=INK,
                   spaceBefore=0, spaceAfter=0)
# spaceBefore rather than a Spacer flowable: ReportLab collapses a
# paragraph's leading space at the top of a frame, so a project that starts a
# fresh page sits against the header instead of floating down it.
S_PROJECT = _style("project", 12.5, 1.25, fontName="Helvetica-Bold", textColor=INK,
                   spaceBefore=10 * mm)
S_CLIENT = _style("client", 9, 1.35, textColor=MUTED)
S_NOTE = _style("note", 8.5, 1.4, textColor=MUTED)
S_BODY = _style("body", 9.5)
S_TH = _style("th", 8, 1.3, fontName="Helvetica-Bold", textColor=PAPER)
S_TH_R = _style("th_r", 8, 1.3, fontName="Helvetica-Bold", textColor=PAPER,
                alignment=TA_RIGHT)
S_TH_C = _style("th_c", 8, 1.3, fontName="Helvetica-Bold", textColor=PAPER,
                alignment=TA_CENTER)
S_CELL = _style("cell", 9)
S_CELL_SM = _style("cell_sm", 8.2, 1.35, textColor=INK_SOFT)
S_CELL_R = _style("cell_r", 9, alignment=TA_RIGHT)
S_CELL_C = _style("cell_c", 9, alignment=TA_CENTER)
S_TOTAL = _style("total", 9.5, 1.3, fontName="Helvetica-Bold")
S_TOTAL_R = _style("total_r", 9.5, 1.3, fontName="Helvetica-Bold", alignment=TA_RIGHT)
S_KPI_VALUE = _style("kpi_value", 17, 1.15, fontName="Helvetica-Bold",
                     textColor=INK, alignment=TA_CENTER)
S_KPI_LABEL = _style("kpi_label", 7, 1.3, textColor=MUTED, alignment=TA_CENTER)
S_META_KEY = _style("meta_key", 8, 1.35, fontName="Helvetica-Bold", textColor=MUTED)
S_META_VAL = _style("meta_val", 9.5, 1.35, textColor=INK)
S_EMPTY = _style("empty", 10, 1.4, textColor=MUTED, alignment=TA_LEFT)


def P(text, style=S_CELL) -> Paragraph:
    """Escaped paragraph — the only way text enters this document."""
    return Paragraph(escape("" if text is None else str(text)), style)


# ── Page furniture ───────────────────────────────────────────────────────────

class _ReportCanvas(pdfcanvas.Canvas):
    """
    Canvas that draws the running header and footer, in two passes.

    The page count is not known until the story has been laid out, so pages are
    buffered and the furniture is stamped on once the total is known — that is
    what makes "Page 3 of 7" possible.
    """

    header_line = ""
    footer_line = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._draw_furniture(total)
            super().showPage()
        super().save()

    def _draw_furniture(self, total_pages: int):
        page = self._pageNumber

        # ── Header ────────────────────────────────────────────────────────
        top = PAGE_H - 13 * mm
        self._draw_mark(MARGIN_X + 2.6 * mm, top + 0.4 * mm, 2.9 * mm)

        self.setFont("Helvetica-Bold", 8.5)
        self.setFillColor(INK)
        self.drawString(MARGIN_X + 8.5 * mm, top - 1.3 * mm, APP_NAME.upper())

        if self.header_line:
            self.setFont("Helvetica", 8.5)
            self.setFillColor(MUTED)
            self.drawRightString(PAGE_W - MARGIN_X, top - 1.3 * mm, self.header_line)

        self.setStrokeColor(RULE)
        self.setLineWidth(0.6)
        self.line(MARGIN_X, top - 5 * mm, PAGE_W - MARGIN_X, top - 5 * mm)
        self.setStrokeColor(ACCENT)
        self.setLineWidth(1.4)
        self.line(MARGIN_X, top - 5 * mm, MARGIN_X + 22 * mm, top - 5 * mm)

        # ── Footer ────────────────────────────────────────────────────────
        base = MARGIN_BOTTOM - 8 * mm
        self.setStrokeColor(RULE)
        self.setLineWidth(0.6)
        self.line(MARGIN_X, base + 5 * mm, PAGE_W - MARGIN_X, base + 5 * mm)

        self.setFont("Helvetica", 7.5)
        self.setFillColor(FAINT)
        self.drawString(MARGIN_X, base + 1.2 * mm, self.footer_line)
        self.drawRightString(PAGE_W - MARGIN_X, base + 1.2 * mm,
                             f"Page {page} of {total_pages}")

    def _draw_mark(self, cx: float, cy: float, r: float):
        """The WorkTrack clock mark, drawn as vectors so it stays crisp."""
        self.saveState()
        self.setFillColor(ACCENT)
        self.circle(cx, cy, r, stroke=0, fill=1)
        self.setFillColor(PAPER)
        self.circle(cx, cy, r * 0.62, stroke=0, fill=1)
        self.setStrokeColor(ACCENT)
        self.setLineWidth(0.7)
        self.setLineCap(1)
        self.line(cx, cy, cx, cy + r * 0.42)
        self.line(cx, cy, cx + r * 0.34, cy - r * 0.24)
        self.restoreState()


# ── Building blocks ──────────────────────────────────────────────────────────

def _share_bar(fraction: float, width: float, color=ACCENT) -> Table:
    """A proportion bar sized to ``fraction`` of ``width``."""
    fraction = min(max(fraction, 0.0), 1.0)
    filled = max(width * fraction, 0.6 * mm)
    bar = Table([[""]], colWidths=[filled], rowHeights=[2.6 * mm])
    bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    track = Table([[bar]], colWidths=[width], rowHeights=[2.6 * mm])
    track.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RULE_SOFT),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
    ]))
    return track


def _section_heading(text: str, rule: bool = True) -> list:
    """A section title with a hairline beneath it, kept as one unit."""
    parts = [P(text, S_SECTION), Spacer(1, 1.6 * mm)]
    if rule:
        line = Table([[""]], colWidths=[CONTENT_W], rowHeights=[0.6])
        line.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        parts.append(line)
    parts.append(Spacer(1, 3 * mm))
    return parts


def _zebra(first_row: int, last_row: int) -> list:
    return [("ROWBACKGROUNDS", (0, first_row), (-1, last_row), [PAPER, ZEBRA])]


# ── Report assembly ──────────────────────────────────────────────────────────

def _cover(worker: str, company: str, period: str, generated: str,
           project_count: int) -> list:
    story: list = [
        P("Timesheet Report", S_TITLE),
        Spacer(1, 1.5 * mm),
        P("Recorded time, summarised by project and task", S_SUBTITLE),
        Spacer(1, 6 * mm),
    ]

    # Label above value, all on one band.  The weights hint at how much room
    # each value tends to need, so a long organisation name does not squeeze
    # the date into wrapping.
    fields = [("Prepared by", worker or "—", 1.1)]
    if company:
        fields.append(("Organisation", company, 1.15))
    fields += [
        ("Reporting period", period, 1.3),
        ("Projects", str(project_count), 0.65),
        ("Generated", generated, 1.2),
    ]

    total_weight = sum(weight for _, _, weight in fields)
    widths = [CONTENT_W * weight / total_weight for _, _, weight in fields]

    meta = Table(
        [[P(label.upper(), S_META_KEY) for label, _, _ in fields],
         [P(value, S_META_VAL) for _, value, _ in fields]],
        colWidths=widths,
    )
    meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT_SOFT),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBEFORE", (0, 0), (0, -1), 1.8, ACCENT),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
    ]))
    story += [meta, Spacer(1, 7 * mm)]
    return story


def _kpi_band(total_seconds: float, session_count: int, project_count: int,
              task_count: int, day_count: int) -> list:
    average = total_seconds / day_count if day_count else 0.0
    tiles = [
        (format_hours_decimal(total_seconds, 2), "TOTAL HOURS"),
        (format_hm(total_seconds), "HOURS (H:MM)"),
        (str(session_count), "SESSIONS"),
        (f"{project_count} / {task_count}", "PROJECTS / TASKS"),
        (format_hours_decimal(average, 2), "AVG HOURS / DAY"),
    ]
    width = CONTENT_W / len(tiles)
    table = Table(
        [[P(value, S_KPI_VALUE) for value, _ in tiles],
         [P(caption, S_KPI_LABEL) for _, caption in tiles]],
        colWidths=[width] * len(tiles),
    )
    table.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.6, RULE),
        ("TEXTCOLOR", (0, 0), (0, 0), ACCENT),
    ]))
    return [table, Spacer(1, 8 * mm)]


def _summary_table(project_rows: list[dict], grand_total: float) -> list:
    widths = [50 * mm, 34 * mm, 24 * mm, 20 * mm, 20 * mm, 26 * mm]
    assert abs(sum(widths) - CONTENT_W) < 0.5, "summary columns must fill the frame"

    header = [P("Project", S_TH), P("Client", S_TH), P("Share", S_TH),
              P("Sessions", S_TH_C), P("H:MM", S_TH_R), P("Hours", S_TH_R)]
    data = [header]
    for row in project_rows:
        share = (row["total_seconds"] / grand_total) if grand_total else 0.0
        data.append([
            P(row["project_name"], S_CELL),
            P(row["client"] or "—", S_CELL_SM),
            _share_bar(share, 24 * mm - 12, row["color"]),
            P(str(row["session_count"]), S_CELL_C),
            P(format_hm(row["total_seconds"]), S_CELL_R),
            P(format_hours_decimal(row["total_seconds"]), S_CELL_R),
        ])
    data.append([
        P("Total", S_TOTAL), "", "",
        P(str(sum(r["session_count"] for r in project_rows)), S_CELL_C),
        P(format_hm(grand_total), S_TOTAL_R),
        P(format_hours_decimal(grand_total), S_TOTAL_R),
    ])

    last = len(data) - 1
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, last - 1), 0.4, RULE_SOFT),
        ("LINEABOVE", (0, last), (-1, last), 0.9, INK),
        ("TEXTCOLOR", (4, last), (-1, last), ACCENT),
        *_zebra(1, last - 1),
    ]))
    return [table, Spacer(1, 9 * mm)]


def _task_table(rows: list[dict], project_total: float) -> Table:
    widths = [102 * mm, 20 * mm, 20 * mm, 32 * mm]
    assert abs(sum(widths) - CONTENT_W) < 0.5, "task columns must fill the frame"

    data = [[P("Task", S_TH), P("Sessions", S_TH_C), P("H:MM", S_TH_R),
             P("Hours (decimal)", S_TH_R)]]
    for row in rows:
        data.append([
            P(row["subtask_name"], S_CELL),
            P(str(row["session_count"]), S_CELL_C),
            P(format_hm(row["total_seconds"]), S_CELL_R),
            P(format_hours_decimal(row["total_seconds"]), S_CELL_R),
        ])
    data.append([
        P("Project total", S_TOTAL),
        P(str(sum(r["session_count"] for r in rows)), S_CELL_C),
        P(format_hm(project_total), S_TOTAL_R),
        P(format_hours_decimal(project_total), S_TOTAL_R),
    ])

    last = len(data) - 1
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, last - 1), 0.4, RULE_SOFT),
        ("LINEABOVE", (0, last), (-1, last), 0.8, INK),
        ("TEXTCOLOR", (2, last), (-1, last), ACCENT),
        *_zebra(1, last - 1),
    ]))
    return table


def _session_table(sessions: list) -> Table:
    # An empty Notes column would waste a quarter of the page width, so it is
    # only reserved when at least one session in this project has a note.
    has_notes = any((s.notes or "").strip() for s in sessions)
    notes_w = 44 * mm if has_notes else 0.0
    widths = [21 * mm, 14 * mm, 14 * mm, 107 * mm - notes_w, 18 * mm]
    header = [P("Date", S_TH), P("Start", S_TH_C), P("End", S_TH_C),
              P("Task", S_TH), P("H:MM", S_TH_R)]
    if has_notes:
        widths.append(notes_w)
        header.append(P("Notes", S_TH))
    assert abs(sum(widths) - CONTENT_W) < 0.5, "session columns must fill the frame"

    data = [header]
    for s in sessions:
        row = [
            P(fmt_date(s.started_at, "%d %b %Y"), S_CELL_SM),
            P(fmt_time(s.started_at), S_CELL_C),
            P(fmt_time(s.ended_at) or "—", S_CELL_C),
            P(s.subtask_name, S_CELL_SM),
            P(format_hm(s.duration_seconds), S_CELL_R),
        ]
        if has_notes:
            row.append(P(s.notes or "", S_CELL_SM))
        data.append(row)

    last = len(data) - 1
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK_SOFT),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, last), 0.4, RULE_SOFT),
        *_zebra(1, last),
    ]))
    return table


#: Space a heading must have beneath it before it is allowed to start a page.
#: Roughly a table header band plus two rows — enough that a title is never
#: the last thing printed on a page.
ORPHAN_GUARD = 34 * mm

S_LOG = _style("log", 9, 1.3, fontName="Helvetica-Bold", textColor=MUTED,
               spaceBefore=6 * mm)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _project_block(project: Project, rows: list[dict], sessions: list,
                   include_sessions: bool) -> list:
    total = sum(r["total_seconds"] for r in rows)
    subtitle = " · ".join(part for part in (
        project.client,
        _plural(len(rows), "task"),
        _plural(sum(r["session_count"] for r in rows), "session"),
    ) if part)

    # CondPageBreak reserves room *below* the heading, so a project title and
    # the start of its table always land on the same page; the table itself
    # then splits freely, repeating its header row on each continuation.
    story: list = [CondPageBreak(ORPHAN_GUARD + 14 * mm), P(project.name, S_PROJECT),
                   P(subtitle, S_CLIENT)]
    if project.notes:
        story += [Spacer(1, 1 * mm), P(project.notes, S_NOTE)]
    story += [Spacer(1, 2.5 * mm), _task_table(rows, total)]

    if include_sessions and sessions:
        story += [
            CondPageBreak(ORPHAN_GUARD),
            P("Session log", S_LOG),
            Spacer(1, 2 * mm),
            _session_table(sessions),
        ]
    return story


def export_pdf(
    db: Database,
    projects: list[Project],
    date_from: Optional[str],
    date_to: Optional[str],
    worker: str,
    output_path: str,
    company: str = "",
    include_sessions: bool = True,
):
    """Write a timesheet report covering ``projects`` over the given period."""
    project_ids = [p.id for p in projects]
    by_project: dict[int, list[dict]] = {}
    for row in db.get_sessions_summary(date_from, date_to, project_ids):
        by_project.setdefault(row["project_id"], []).append(row)

    ordered = sorted(
        (p for p in projects if by_project.get(p.id)),
        key=lambda p: -sum(r["total_seconds"] for r in by_project[p.id]),
    )

    grand_total = sum(r["total_seconds"]
                      for rows in by_project.values() for r in rows)
    session_count = sum(r["session_count"]
                        for rows in by_project.values() for r in rows)
    task_count = sum(len(rows) for rows in by_project.values())
    day_count = len(db.get_daily_totals(date_from, date_to, project_ids))

    period = period_label(date_from, date_to)
    generated = now_local().strftime("%d %B %Y, %H:%M")

    # ── Document shell ────────────────────────────────────────────────────
    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN_X, rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM,
        title=f"{APP_NAME} Timesheet Report",
        author=worker or APP_NAME,
        subject=f"Timesheet — {period}",
        creator=f"{APP_NAME} {__version__}",
    )
    frame = Frame(
        MARGIN_X, MARGIN_BOTTOM, CONTENT_W,
        PAGE_H - MARGIN_TOP - MARGIN_BOTTOM,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        id="body",
    )
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame])])

    footer_bits = [f"{APP_NAME} timesheet"]
    if worker:
        footer_bits.append(worker)
    footer_bits.append(period)

    class ReportCanvas(_ReportCanvas):
        header_line = period
        footer_line = "  ·  ".join(footer_bits)

    # ── Story ─────────────────────────────────────────────────────────────
    story: list = _cover(worker, company, period, generated, len(ordered))

    if not ordered:
        story += [
            P("No time was recorded in this period.", S_EMPTY),
            Spacer(1, 2 * mm),
            P("Adjust the date range on the Exports screen and try again.", S_NOTE),
        ]
        doc.build(story, canvasmaker=ReportCanvas)
        return

    story += _kpi_band(grand_total, session_count, len(ordered), task_count, day_count)

    summary_rows = []
    for project in ordered:
        rows = by_project[project.id]
        summary_rows.append({
            "project_name": project.name,
            "client": project.client,
            "color": colors.HexColor(project.color or "#2C7BE5"),
            "session_count": sum(r["session_count"] for r in rows),
            "total_seconds": sum(r["total_seconds"] for r in rows),
        })

    story += _section_heading("Summary by project")
    story += _summary_table(summary_rows, grand_total)

    story += [CondPageBreak(50 * mm)]
    story += _section_heading("Detail by project")

    for project in ordered:
        sessions = (
            sorted(db.get_sessions(project_id=project.id, date_from=date_from,
                                   date_to=date_to),
                   key=lambda s: parse_local(s.started_at) or now_local())
            if include_sessions else []
        )
        story += _project_block(project, by_project[project.id], sessions,
                                include_sessions)

    doc.build(story, canvasmaker=ReportCanvas)

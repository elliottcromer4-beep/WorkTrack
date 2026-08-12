"""
WorkTrack colour palette and font constants.
Dark professional theme — Mantine-inspired.
"""

# ── Background levels ─────────────────────────────────────────────────────────
BG_BASE      = "#141517"   # window chrome
BG_RAISED    = "#1A1B1E"   # main panels
BG_CARD      = "#25262B"   # cards / list rows
BG_HOVER     = "#2C2E33"   # hover
BG_ACTIVE    = "#373A40"   # selected / pressed

# ── Borders ───────────────────────────────────────────────────────────────────
BORDER       = "#373A40"
BORDER_LIGHT = "#484B50"

# ── Text ──────────────────────────────────────────────────────────────────────
TEXT         = "#C1C2C5"
TEXT_BRIGHT  = "#E5E7EB"
TEXT_MUTED   = "#6B6F77"
TEXT_DIM     = "#4E5158"

# ── Accent ────────────────────────────────────────────────────────────────────
ACCENT       = "#4DABF7"
ACCENT_HOVER = "#74C0FC"
ACCENT_BG    = "#1C3048"

# ── Status colours ────────────────────────────────────────────────────────────
SUCCESS      = "#69DB7C"
SUCCESS_BG   = "#1A3128"
WARNING      = "#FFD43B"
WARNING_BG   = "#2E2708"
DANGER       = "#FF6B6B"
DANGER_BG    = "#3B1A1A"

# ── Timer display ─────────────────────────────────────────────────────────────
TIMER_RUN    = "#69DB7C"
TIMER_PAUSE  = "#FFD43B"
TIMER_STOP   = "#6B6F77"

# ── Button presets ────────────────────────────────────────────────────────────
# Deliberately no corner_radius: it is passed per call, so a caller can set it
# without colliding with the preset.
BTN_PRIMARY = {
    "fg_color": ACCENT, "hover_color": ACCENT_HOVER, "text_color": "white",
}
BTN_DANGER = {
    "fg_color": DANGER, "hover_color": "#FF8787", "text_color": "white",
}
BTN_SUCCESS = {
    "fg_color": SUCCESS, "hover_color": "#8CE99A", "text_color": "#1A1B1E",
}
BTN_GHOST = {
    "fg_color": BG_CARD, "hover_color": BG_HOVER, "text_color": TEXT,
    "border_width": 1, "border_color": BORDER,
}
BTN_TRANS = {
    "fg_color": "transparent", "hover_color": BG_HOVER, "text_color": TEXT,
}

# ── Informational ─────────────────────────────────────────────────────────────
INFO         = ACCENT
INFO_BG      = ACCENT_BG

# ── Fonts ─────────────────────────────────────────────────────────────────────
FONT_TINY    = ("Segoe UI", 9)
FONT_SMALL   = ("Segoe UI", 10)
FONT_BASE    = ("Segoe UI", 12)
FONT_MED     = ("Segoe UI", 13)
FONT_LARGE   = ("Segoe UI", 14)
FONT_LABEL   = ("Segoe UI", 10, "bold")
FONT_TITLE   = ("Segoe UI", 16, "bold")
FONT_HEADING = ("Segoe UI", 20, "bold")
FONT_MONO    = ("Consolas", 11)
FONT_MONO_L  = ("Consolas", 13)
FONT_TIMER   = ("Consolas", 34, "bold")
FONT_TIMER_S = ("Consolas", 20, "bold")

# ── Geometry ──────────────────────────────────────────────────────────────────
RADIUS_SM    = 6
RADIUS_MD    = 8
RADIUS_LG    = 12

# ── Project palette (cycle for new projects) ──────────────────────────────────
PROJECT_COLORS = [
    "#4DABF7",  # blue
    "#69DB7C",  # green
    "#FF6B6B",  # red
    "#FFD43B",  # yellow
    "#CC5DE8",  # purple
    "#FF922B",  # orange
    "#20C997",  # teal
    "#F06595",  # pink
]

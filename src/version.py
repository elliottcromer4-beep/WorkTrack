"""Single source of truth for application identity and version."""

__version__ = "1.1.0"

APP_NAME = "WorkTrack"
APP_TAGLINE = "Professional Time Tracking"
APP_AUTHOR = "Elliott Cromer"
APP_COPYRIGHT = "Copyright (c) 2026 Elliott Cromer"
APP_DESCRIPTION = "Time tracking and timesheet reporting for professional services."

# Version tuple, used by the Windows executable version resource.
VERSION_TUPLE = tuple(int(part) for part in __version__.split(".")) + (0,)

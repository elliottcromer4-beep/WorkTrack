#!/usr/bin/env python3
"""
WorkTrack — entry point.

Runs the app, and makes sure the two things that go wrong outside the app's
control are handled visibly: a second copy being launched, and an unexpected
crash in a windowed build where there is no console to print to.
"""
import argparse
import sys
import traceback
from pathlib import Path

# Allow `python main.py` from a checkout, where `src` is not installed.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.system import SingleInstance, data_dir  # noqa: E402
from src.version import APP_NAME, __version__  # noqa: E402


def _alert(title: str, message: str):
    """Show a message without assuming a console exists."""
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(f"{title}: {message}", file=sys.stderr)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME.lower(), description=APP_NAME)
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--data-dir", type=Path, default=None,
        help="use this folder for the database, backups and exports instead of "
             "the default per-user location (useful for a portable install)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    directory = args.data_dir.expanduser().resolve() if args.data_dir else data_dir(
        APP_NAME)
    directory.mkdir(parents=True, exist_ok=True)

    lock = SingleInstance(directory / "worktrack.lock")
    if not lock.acquire():
        _alert(
            APP_NAME,
            f"{APP_NAME} is already running.\n\n"
            "Look for the clock icon in the system tray, or the floating timer "
            "widget on your desktop.",
        )
        return 1

    try:
        from src.ui.app import AppController

        AppController(data_directory=directory).run()
        return 0
    except Exception:
        report = directory / "crash.log"
        details = traceback.format_exc()
        try:
            report.write_text(
                f"{APP_NAME} {__version__}\n\n{details}", encoding="utf-8"
            )
        except OSError:
            pass
        _alert(
            APP_NAME,
            f"{APP_NAME} could not start.\n\nDetails were written to:\n{report}",
        )
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())

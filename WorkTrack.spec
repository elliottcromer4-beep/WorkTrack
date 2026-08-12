# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build.

    pyinstaller WorkTrack.spec

The Windows version resource is generated here from src/version.py, so the
properties Explorer shows can never drift from the version the app reports.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, str(Path(SPECPATH)))
from src.version import (  # noqa: E402
    APP_AUTHOR, APP_COPYRIGHT, APP_DESCRIPTION, APP_NAME, VERSION_TUPLE,
    __version__,
)

datas = [("assets", "assets")]
binaries = []
hiddenimports = [
    "customtkinter",
    "PIL._tkinter_finder",
    "pystray._win32",
    "reportlab",
    "openpyxl",
]

bundled = collect_all("customtkinter")
datas += bundled[0]
binaries += bundled[1]
hiddenimports += bundled[2]


def windows_version_resource():
    """Build the VERSIONINFO block Explorer reads from the .exe properties."""
    if sys.platform != "win32":
        return None
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
        VarStruct, VSVersionInfo,
    )

    strings = [
        StringStruct("CompanyName", APP_AUTHOR),
        StringStruct("FileDescription", APP_DESCRIPTION),
        StringStruct("FileVersion", __version__),
        StringStruct("InternalName", APP_NAME),
        StringStruct("LegalCopyright", APP_COPYRIGHT),
        StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
        StringStruct("ProductName", APP_NAME),
        StringStruct("ProductVersion", __version__),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE,
                          mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("040904B0", strings)]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "fitz", "pymupdf"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(Path("assets") / "worktrack.ico")],
    version=windows_version_resource(),
)

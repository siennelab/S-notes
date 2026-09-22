import os
from pathlib import Path


ROOT = Path(SPECPATH).resolve().parent
SVG_DIR = ROOT / "src" / "icons"
APP_ICON = ROOT / "build" / "S-notes.icns"
if os.environ.get("S_NOTES_APP_ICON"):
    APP_ICON = Path(os.environ["S_NOTES_APP_ICON"])
HELPER_BINARY = Path(
    os.environ.get(
        "S_NOTES_HELPER_BINARY",
        str(ROOT / "build" / "helper-dist" / "snotes-gale-production-pull"),
    )
)
REMINDER_HELPER_BINARY = Path(
    os.environ.get(
        "S_NOTES_REMINDER_HELPER_BINARY",
        str(ROOT / "build" / "reminder-helper-dist" / "snotes-reminder-helper"),
    )
)


analysis = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(SVG_DIR), "src/icons"),
        (str(ROOT / "web/markdown-editor/dist"), "web/markdown-editor/dist"),
        (str(HELPER_BINARY), "bin"),
        (str(REMINDER_HELPER_BINARY), "bin"),
    ],
    hiddenimports=["PySide6.QtSvg", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="S-notes",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)

collected = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="S-notes",
)

app = BUNDLE(
    collected,
    name="S-notes.app",
    icon=str(APP_ICON),
    bundle_identifier="de.ke-den.snotes",
    version="1.0.0",
    info_plist={
        "CFBundleName": "S-notes",
        "CFBundleDisplayName": "S-notes",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
    },
)

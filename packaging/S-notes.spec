import os
from pathlib import Path


ROOT = Path(SPECPATH).resolve().parent
SVG_DIR = ROOT / "src" / "icons"
MCP_LAUNCHER = ROOT / "packaging" / "run_snotes_mcp_stdio.sh"
YAN_MCP_LAUNCHER = ROOT / "scripts" / "run_snotes_yan_mcp_stdio.sh"
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
BUNDLE_IDENTIFIER = os.environ.get(
    "S_NOTES_BUNDLE_IDENTIFIER", "de.ke-den.snotes"
)
RETIRE_REMINDER_HELPER = (
    os.environ.get("S_NOTES_RETIRE_REMINDER_HELPER") == "1"
)
# Runtime Reminder authority is a build input carried in Info.plist; a packaged
# App never relies on the build shell's environment.
REMINDER_AUTHORITY = os.environ.get("S_NOTES_REMINDER_AUTHORITY", "")
REMOTE_REMINDER_ENDPOINT = os.environ.get("S_NOTES_REMOTE_REMINDER_ENDPOINT", "")
if REMINDER_AUTHORITY not in {"local", "remote"} or (
    (REMINDER_AUTHORITY == "remote") != bool(REMOTE_REMINDER_ENDPOINT)
):
    raise SystemExit("An explicit Reminder authority (and remote endpoint) is required.")
REMINDER_AUTHORITY_INFO = {
    "SNotesReminderAuthority": REMINDER_AUTHORITY,
    "SNotesReminderHelperRetired": RETIRE_REMINDER_HELPER,
}
if REMOTE_REMINDER_ENDPOINT:
    REMINDER_AUTHORITY_INFO["SNotesRemoteReminderEndpoint"] = REMOTE_REMINDER_ENDPOINT


analysis = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(SVG_DIR), "src/icons"),
        (str(ROOT / "web/markdown-editor/dist"), "web/markdown-editor/dist"),
        (str(HELPER_BINARY), "bin"),
        (str(MCP_LAUNCHER), "bin"),
        (str(YAN_MCP_LAUNCHER), "bin"),
    ] + (
        []
        if RETIRE_REMINDER_HELPER
        else [(str(REMINDER_HELPER_BINARY), "bin")]
    ),
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
    bundle_identifier=BUNDLE_IDENTIFIER,
    version="1.0.0",
    info_plist={
        "CFBundleName": "S-notes",
        "CFBundleDisplayName": "S-notes",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "SNotesSourceCommit": os.environ.get("S_NOTES_SOURCE_COMMIT", "unknown"),
        "SNotesSourceDirty": os.environ.get("S_NOTES_SOURCE_DIRTY", "unknown"),
        "SNotesStep07Candidate": False,
        **REMINDER_AUTHORITY_INFO,
    },
)

from pathlib import Path


ROOT = Path(SPECPATH).resolve().parent
SVG_DIR = ROOT / "src" / "icons"
APP_ICON = ROOT / "build" / "S-notes.icns"


analysis = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(SVG_DIR), "src/icons")],
    hiddenimports=["PySide6.QtSvg"],
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

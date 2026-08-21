from pathlib import Path


ROOT = Path(SPECPATH).resolve().parent


analysis = Analysis(
    [str(ROOT / "reminder_helper.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.QtWidgets",
        "PySide6.QtGui",
        "PySide6.QtNetwork",
    ],
    noarchive=False,
    optimize=0,
)

python_archive = PYZ(analysis.pure)

helper = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="snotes-reminder-helper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)

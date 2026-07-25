#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
SPEC_FILE="$ROOT_DIR/packaging/S-notes.spec"
ICON_SOURCE="$ROOT_DIR/resources/macos/icon/S-notes-icon-source-2048.png"
BUILD_DIR="$ROOT_DIR/build"
DIST_DIR="$ROOT_DIR/dist"
ICONSET_DIR="$BUILD_DIR/S-notes.iconset"
ICNS_FILE="$BUILD_DIR/S-notes.icns"
APP_BUNDLE="$DIST_DIR/S-notes.app"
APP_EXECUTABLE="$APP_BUNDLE/Contents/MacOS/S-notes"

EXPECTED_PYTHON_SERIES="3.13"
EXPECTED_PYSIDE="6.10.3"
EXPECTED_PYINSTALLER="6.21.0"
EXPECTED_HOOKS="2026.6"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

safe_clean() {
    local target="$1"
    case "$target" in
        "$ROOT_DIR/build"|"$ROOT_DIR/dist")
            rm -rf -- "$target"
            ;;
        *)
            fail "Refusing to clean unexpected path: $target"
            ;;
    esac
}

metadata_value() {
    /usr/libexec/PlistBuddy -c "Print :$1" "$APP_BUNDLE/Contents/Info.plist"
}

echo "S-notes macOS arm64 build"
echo "Repository: $ROOT_DIR"

[[ "$(uname -s)" == "Darwin" ]] || fail "This build must run on macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "The current Mac must run as arm64."
[[ -f "$ROOT_DIR/app.py" && -d "$ROOT_DIR/src" ]] || fail "Unexpected repository layout."
[[ -x "$PYTHON" ]] || fail "Project Python not found: $PYTHON"
[[ -f "$SPEC_FILE" ]] || fail "PyInstaller spec not found: $SPEC_FILE"
[[ -f "$ICON_SOURCE" ]] || fail "Official icon source not found: $ICON_SOURCE"

for command_name in sips iconutil file lipo otool codesign du find grep tr awk; do
    require_command "$command_name"
done
[[ -x /usr/libexec/PlistBuddy ]] || fail "PlistBuddy not found."

python_version="$($PYTHON -B -c 'import platform; print(platform.python_version())')"
python_series="$($PYTHON -B -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python_arch="$($PYTHON -B -c 'import platform; print(platform.machine())')"
pyside_version="$($PYTHON -B -c 'import importlib.metadata as m; print(m.version("PySide6"))')"
pyinstaller_version="$($PYTHON -B -c 'import importlib.metadata as m; print(m.version("PyInstaller"))')"
hooks_version="$($PYTHON -B -c 'import importlib.metadata as m; print(m.version("pyinstaller-hooks-contrib"))')"

[[ "$python_series" == "$EXPECTED_PYTHON_SERIES" ]] || fail "Expected Python ${EXPECTED_PYTHON_SERIES}.x, found $python_version."
[[ "$python_arch" == "arm64" ]] || fail "Expected arm64 Python, found $python_arch."
[[ "$pyside_version" == "$EXPECTED_PYSIDE" ]] || fail "Expected PySide6 $EXPECTED_PYSIDE, found $pyside_version."
[[ "$pyinstaller_version" == "$EXPECTED_PYINSTALLER" ]] || fail "Expected PyInstaller $EXPECTED_PYINSTALLER, found $pyinstaller_version."
[[ "$hooks_version" == "$EXPECTED_HOOKS" ]] || fail "Expected pyinstaller-hooks-contrib $EXPECTED_HOOKS, found $hooks_version."

[[ "$(grep -c '^[[:space:]]*datas=' "$SPEC_FILE")" == "1" ]] || fail "The spec must define exactly one datas whitelist."
grep -Fq 'datas=[(str(SVG_DIR), "src/icons")]' "$SPEC_FILE" || fail "The spec datas whitelist must contain only src/icons."
grep -Fq 'target_arch="arm64"' "$SPEC_FILE" || fail "The spec must target arm64."
grep -Fq 'bundle_identifier="de.ke-den.snotes"' "$SPEC_FILE" || fail "The Bundle Identifier is missing from the spec."
grep -Fq '"CFBundleShortVersionString": "1.0.0"' "$SPEC_FILE" || fail "The short version is missing from the spec."
grep -Fq '"CFBundleVersion": "1"' "$SPEC_FILE" || fail "The build version is missing from the spec."
grep -Fq '"LSMinimumSystemVersion": "13.0"' "$SPEC_FILE" || fail "The minimum macOS version is missing from the spec."

icon_metadata="$(sips -g format -g pixelWidth -g pixelHeight -g hasAlpha "$ICON_SOURCE")"
grep -q "format: png" <<<"$icon_metadata" || fail "Official icon must be PNG."
grep -q "pixelWidth: 2048" <<<"$icon_metadata" || fail "Official icon width must be 2048."
grep -q "pixelHeight: 2048" <<<"$icon_metadata" || fail "Official icon height must be 2048."
grep -q "hasAlpha: yes" <<<"$icon_metadata" || fail "Official icon must have an alpha channel."
file "$ICON_SOURCE" | grep -q "2048 x 2048, 8-bit/color RGBA" || fail "Official icon must be 8-bit RGBA."

for warning_path in "$ROOT_DIR/backups" "$ROOT_DIR/S-notes-icon-1024.png" "$ROOT_DIR/S-notes-icon-preview.png"; do
    if [[ -e "$warning_path" ]]; then
        echo "WARNING: ignored local item will not enter the App: $warning_path"
    fi
done

safe_clean "$BUILD_DIR"
safe_clean "$DIST_DIR"
mkdir -p "$ICONSET_DIR"

generate_icon() {
    local pixels="$1"
    local filename="$2"
    sips -z "$pixels" "$pixels" "$ICON_SOURCE" --out "$ICONSET_DIR/$filename" >/dev/null
}

generate_icon 16 icon_16x16.png
generate_icon 32 icon_16x16@2x.png
generate_icon 32 icon_32x32.png
generate_icon 64 icon_32x32@2x.png
generate_icon 128 icon_128x128.png
generate_icon 256 icon_128x128@2x.png
generate_icon 256 icon_256x256.png
generate_icon 512 icon_256x256@2x.png
generate_icon 512 icon_512x512.png
generate_icon 1024 icon_512x512@2x.png
iconutil -c icns "$ICONSET_DIR" -o "$ICNS_FILE"

echo "Building S-notes.app..."
cd "$ROOT_DIR"
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --workpath "$BUILD_DIR/pyinstaller" \
    --distpath "$DIST_DIR" \
    "$SPEC_FILE"

[[ -d "$APP_BUNDLE" ]] || fail "App bundle was not created: $APP_BUNDLE"
[[ -x "$APP_EXECUTABLE" ]] || fail "App executable was not created: $APP_EXECUTABLE"

[[ "$(metadata_value CFBundleName)" == "S-notes" ]] || fail "Incorrect CFBundleName."
[[ "$(metadata_value CFBundleDisplayName)" == "S-notes" ]] || fail "Incorrect CFBundleDisplayName."
[[ "$(metadata_value CFBundleIdentifier)" == "de.ke-den.snotes" ]] || fail "Incorrect CFBundleIdentifier."
[[ "$(metadata_value CFBundleShortVersionString)" == "1.0.0" ]] || fail "Incorrect short version."
[[ "$(metadata_value CFBundleVersion)" == "1" ]] || fail "Incorrect build version."
[[ "$(metadata_value LSMinimumSystemVersion)" == "13.0" ]] || fail "Incorrect minimum macOS version."

executable_archs="$(lipo -archs "$APP_EXECUTABLE")"
[[ "$executable_archs" == "arm64" ]] || fail "Expected arm64 executable, found: $executable_archs"
[[ -f "$APP_BUNDLE/Contents/Resources/S-notes.icns" ]] || fail "App icon is missing from the bundle."

qt_platform_plugin="$(find "$APP_BUNDLE" -type f -name 'libqcocoa.dylib' -print -quit)"
[[ -n "$qt_platform_plugin" ]] || fail "Qt macOS platform plugin libqcocoa.dylib is missing."

while IFS= read -r -d '' bundle_item; do
    relative_path="${bundle_item#"$APP_BUNDLE"/}"
    lower_path="$(printf '%s' "$relative_path" | tr '[:upper:]' '[:lower:]')"
    case "/$lower_path/" in
        *.db/|*.sqlite/|*.sqlite3/|*/.env*/|*/local_redaction_rules.json/|*/data/*|*/backups/*|*/exports/*|*/imports/*|*/config/*|*/tests/*|*/readme.md/|*/.git/*|*/.venv/*|*/s-notes-icon-preview.png/|*/s-notes-icon-1024.png/|*.pem/|*.key/|*api_key*|*apikey*|*token*|*password*|*cookie*|*secret*)
            fail "Prohibited item found in App bundle: $relative_path"
            ;;
    esac
done < <(find "$APP_BUNDLE" -print0)

while IFS= read -r -d '' candidate; do
    if file "$candidate" | grep -q "Mach-O"; then
        load_commands="$(otool -L "$candidate" 2>/dev/null | grep '^[[:space:]]' || true)"
        rpaths="$(otool -l "$candidate" 2>/dev/null | grep -A2 LC_RPATH || true)"
        if grep -Fq "$ROOT_DIR" <<<"$load_commands$rpaths" || grep -q '/Users/' <<<"$load_commands$rpaths"; then
            fail "Repository or user-specific Mach-O load path found: ${candidate#"$APP_BUNDLE"/}"
        fi
    fi
done < <(find "$APP_BUNDLE" -type f -print0)

codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

app_size="$(du -sh "$APP_BUNDLE" | awk '{print $1}')"
echo
echo "BUILD SUCCESS"
echo "App: $APP_BUNDLE"
echo "Size: $app_size"
echo "Architecture: $executable_archs"
echo "Qt platform plugin: ${qt_platform_plugin#"$APP_BUNDLE"/}"
echo "Signature: codesign verification passed (ad-hoc)"
echo "The App was not launched or installed."

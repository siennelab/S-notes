#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
SPEC_FILE="$ROOT_DIR/packaging/S-notes.spec"
HELPER_SPEC_FILE="$ROOT_DIR/packaging/snotes-gale-production-pull.spec"
REMINDER_HELPER_SPEC_FILE="$ROOT_DIR/packaging/snotes-reminder-helper.spec"
ICON_SOURCE="$ROOT_DIR/resources/macos/icon/S-notes-icon-source-2048.png"
BUILD_DIR="$ROOT_DIR/build"
DIST_DIR="$ROOT_DIR/dist"
STEP07_CANDIDATE="0"
BUNDLE_IDENTIFIER="de.ke-den.snotes"
if [[ "${1:-}" == "--candidate" ]]; then
    BUILD_DIR="$ROOT_DIR/build-d1-candidate"
    DIST_DIR="$ROOT_DIR/dist-d1-candidate"
elif [[ "${1:-}" == "--step07-candidate" ]]; then
    BUILD_DIR="$ROOT_DIR/build-step07-candidate"
    DIST_DIR="$ROOT_DIR/dist-step07-candidate"
    STEP07_CANDIDATE="1"
    BUNDLE_IDENTIFIER="de.ke-den.snotes.step07candidate"
elif [[ $# -gt 0 ]]; then
    echo "Usage: $0 [--candidate|--step07-candidate]" >&2
    exit 2
fi
RETIRE_REMINDER_HELPER="${S_NOTES_RETIRE_REMINDER_HELPER:-0}"
if [[ "$RETIRE_REMINDER_HELPER" != "0" && "$RETIRE_REMINDER_HELPER" != "1" ]]; then
    echo "S_NOTES_RETIRE_REMINDER_HELPER must be 0 or 1." >&2
    exit 2
fi
ICONSET_DIR="$BUILD_DIR/S-notes.iconset"
ICNS_FILE="$BUILD_DIR/S-notes.icns"
HELPER_WORK_DIR="$BUILD_DIR/helper-pyinstaller"
HELPER_DIST_DIR="$BUILD_DIR/helper-dist"
HELPER_EXECUTABLE="$HELPER_DIST_DIR/snotes-gale-production-pull"
REMINDER_HELPER_WORK_DIR="$BUILD_DIR/reminder-helper-pyinstaller"
REMINDER_HELPER_DIST_DIR="$BUILD_DIR/reminder-helper-dist"
REMINDER_HELPER_EXECUTABLE="$REMINDER_HELPER_DIST_DIR/snotes-reminder-helper"
APP_BUNDLE="$DIST_DIR/S-notes.app"
APP_EXECUTABLE="$APP_BUNDLE/Contents/MacOS/S-notes"
APP_HELPER="$APP_BUNDLE/Contents/Resources/bin/snotes-gale-production-pull"
APP_REMINDER_HELPER="$APP_BUNDLE/Contents/Resources/bin/snotes-reminder-helper"
APP_MCP_LAUNCHER="$APP_BUNDLE/Contents/Resources/bin/run_snotes_mcp_stdio.sh"
APP_YAN_MCP_LAUNCHER="$APP_BUNDLE/Contents/Resources/bin/run_snotes_yan_mcp_stdio.sh"
MCP_LAUNCHER_SOURCE="$ROOT_DIR/packaging/run_snotes_mcp_stdio.sh"
YAN_MCP_LAUNCHER_SOURCE="$ROOT_DIR/scripts/run_snotes_yan_mcp_stdio.sh"

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
        "$ROOT_DIR/build"|"$ROOT_DIR/dist"|\
        "$ROOT_DIR/build-d1-candidate"|"$ROOT_DIR/dist-d1-candidate"|\
        "$ROOT_DIR/build-step07-candidate"|"$ROOT_DIR/dist-step07-candidate")
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
[[ -f "$ROOT_DIR/app.py" && -d "$ROOT_DIR/src" && -e "$ROOT_DIR/.git" ]] || fail "Unexpected repository layout."
[[ -x "$PYTHON" ]] || fail "Project Python not found: $PYTHON"
[[ -f "$SPEC_FILE" ]] || fail "PyInstaller spec not found: $SPEC_FILE"
[[ -f "$HELPER_SPEC_FILE" ]] || fail "Helper PyInstaller spec not found: $HELPER_SPEC_FILE"
if [[ "$RETIRE_REMINDER_HELPER" != "1" ]]; then
    [[ -f "$REMINDER_HELPER_SPEC_FILE" ]] || fail "Reminder helper PyInstaller spec not found: $REMINDER_HELPER_SPEC_FILE"
fi
[[ -x "$MCP_LAUNCHER_SOURCE" ]] || fail "Packaged MCP launcher source is missing or not executable: $MCP_LAUNCHER_SOURCE"
[[ -x "$YAN_MCP_LAUNCHER_SOURCE" ]] || fail "Yan MCP launcher source is missing or not executable: $YAN_MCP_LAUNCHER_SOURCE"
[[ -f "$ICON_SOURCE" ]] || fail "Official icon source not found: $ICON_SOURCE"

for command_name in git npm node sips iconutil file lipo otool codesign du find grep tr awk shasum; do
    require_command "$command_name"
done
[[ -x /usr/libexec/PlistBuddy ]] || fail "PlistBuddy not found."

python_version="$($PYTHON -B -c 'import platform; print(platform.python_version())')"
python_series="$($PYTHON -B -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python_arch="$($PYTHON -B -c 'import platform; print(platform.machine())')"
pyside_version="$($PYTHON -B -c 'import importlib.metadata as m; print(m.version("PySide6"))')"
pyinstaller_version="$($PYTHON -B -c 'import importlib.metadata as m; print(m.version("PyInstaller"))')"
hooks_version="$($PYTHON -B -c 'import importlib.metadata as m; print(m.version("pyinstaller-hooks-contrib"))')"
certifi_ca_source="$($PYTHON -B -c 'import certifi; print(certifi.where())')"
[[ -f "$certifi_ca_source" ]] || fail "certifi public CA bundle was not found: $certifi_ca_source"
certifi_ca_hash="$(shasum -a 256 "$certifi_ca_source" | awk '{print $1}')"
source_commit="$(git -C "$ROOT_DIR" rev-parse HEAD)"
source_dirty="NO"
if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=no)" ]]; then
    source_dirty="YES"
fi

[[ "$python_series" == "$EXPECTED_PYTHON_SERIES" ]] || fail "Expected Python ${EXPECTED_PYTHON_SERIES}.x, found $python_version."
[[ "$python_arch" == "arm64" ]] || fail "Expected arm64 Python, found $python_arch."
[[ "$pyside_version" == "$EXPECTED_PYSIDE" ]] || fail "Expected PySide6 $EXPECTED_PYSIDE, found $pyside_version."
[[ "$pyinstaller_version" == "$EXPECTED_PYINSTALLER" ]] || fail "Expected PyInstaller $EXPECTED_PYINSTALLER, found $pyinstaller_version."
[[ "$hooks_version" == "$EXPECTED_HOOKS" ]] || fail "Expected pyinstaller-hooks-contrib $EXPECTED_HOOKS, found $hooks_version."

[[ "$(grep -c '^[[:space:]]*datas=' "$SPEC_FILE")" == "1" ]] || fail "The spec must define exactly one datas whitelist."
grep -Fq '(str(SVG_DIR), "src/icons")' "$SPEC_FILE" || fail "The App spec must include src/icons."
grep -Fq '(str(HELPER_BINARY), "bin")' "$SPEC_FILE" || fail "The App spec must include the production helper."
grep -Fq '[(str(REMINDER_HELPER_BINARY), "bin")]' "$SPEC_FILE" || fail "The App spec must retain the gated Reminder helper input."
grep -Fq '(str(MCP_LAUNCHER), "bin")' "$SPEC_FILE" || fail "The App spec must include the packaged MCP launcher."
grep -Fq '(str(YAN_MCP_LAUNCHER), "bin")' "$SPEC_FILE" || fail "The App spec must include the Yan MCP launcher."
grep -Fq 'target_arch="arm64"' "$SPEC_FILE" || fail "The spec must target arm64."
grep -Fq 'target_arch="arm64"' "$HELPER_SPEC_FILE" || fail "The helper spec must target arm64."
if [[ "$RETIRE_REMINDER_HELPER" != "1" ]]; then
    grep -Fq 'target_arch="arm64"' "$REMINDER_HELPER_SPEC_FILE" || fail "The Reminder helper spec must target arm64."
fi
grep -Fq 'bundle_identifier=BUNDLE_IDENTIFIER' "$SPEC_FILE" || fail "The Bundle Identifier input is missing from the spec."
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

echo "Building local Markdown editor assets..."
(cd "$ROOT_DIR/web/markdown-editor" && npm ci --no-audit --no-fund && npm run build)
[[ -f "$ROOT_DIR/web/markdown-editor/dist/index.html" ]] || fail "Markdown bundle index.html missing."

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
if ! iconutil -c icns "$ICONSET_DIR" -o "$ICNS_FILE"; then
    echo "WARNING: iconutil rejected a validated iconset; using deterministic ICNS fallback."
    "$PYTHON" -B "$ROOT_DIR/scripts/build_icns.py" "$ICONSET_DIR" "$ICNS_FILE"
fi

echo "Building standalone production pull helper..."
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --workpath "$HELPER_WORK_DIR" \
    --distpath "$HELPER_DIST_DIR" \
    "$HELPER_SPEC_FILE"

[[ -x "$HELPER_EXECUTABLE" ]] || fail "Production helper was not created: $HELPER_EXECUTABLE"
[[ "$(lipo -archs "$HELPER_EXECUTABLE")" == "arm64" ]] || fail "Production helper is not arm64."
[[ "$("$HELPER_EXECUTABLE" --version)" == "snotes-gale-production-pull 1" ]] || fail "Production helper version check failed."
codesign --verify --strict --verbose=2 "$HELPER_EXECUTABLE"

if [[ "$RETIRE_REMINDER_HELPER" != "1" ]]; then
    echo "Building standalone Reminder helper..."
    "$PYTHON" -m PyInstaller \
        --noconfirm \
        --clean \
        --workpath "$REMINDER_HELPER_WORK_DIR" \
        --distpath "$REMINDER_HELPER_DIST_DIR" \
        "$REMINDER_HELPER_SPEC_FILE"

    [[ -x "$REMINDER_HELPER_EXECUTABLE" ]] || fail "Reminder helper was not created: $REMINDER_HELPER_EXECUTABLE"
    [[ "$(lipo -archs "$REMINDER_HELPER_EXECUTABLE")" == "arm64" ]] || fail "Reminder helper is not arm64."
    [[ "$("$REMINDER_HELPER_EXECUTABLE" --version)" == "snotes-reminder-helper 1" ]] || fail "Reminder helper version check failed."
    codesign --verify --strict --verbose=2 "$REMINDER_HELPER_EXECUTABLE"
fi

icns_metadata="$(sips -g format -g pixelWidth -g pixelHeight "$ICNS_FILE")"
grep -q "format: icns" <<<"$icns_metadata" || fail "Generated App icon must be ICNS."
grep -q "pixelWidth: 1024" <<<"$icns_metadata" || fail "Generated App icon width must be 1024."
grep -q "pixelHeight: 1024" <<<"$icns_metadata" || fail "Generated App icon height must be 1024."

echo "Building S-notes.app..."
cd "$ROOT_DIR"
env \
    S_NOTES_APP_ICON="$ICNS_FILE" \
    S_NOTES_HELPER_BINARY="$HELPER_EXECUTABLE" \
    S_NOTES_REMINDER_HELPER_BINARY="$REMINDER_HELPER_EXECUTABLE" \
    S_NOTES_RETIRE_REMINDER_HELPER="$RETIRE_REMINDER_HELPER" \
    S_NOTES_STEP07_CANDIDATE="$STEP07_CANDIDATE" \
    S_NOTES_BUNDLE_IDENTIFIER="$BUNDLE_IDENTIFIER" \
    S_NOTES_SOURCE_COMMIT="$source_commit" \
    S_NOTES_SOURCE_DIRTY="$source_dirty" \
    "$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --workpath "$BUILD_DIR/pyinstaller" \
    --distpath "$DIST_DIR" \
    "$SPEC_FILE"

[[ -d "$APP_BUNDLE" ]] || fail "App bundle was not created: $APP_BUNDLE"
[[ -x "$APP_EXECUTABLE" ]] || fail "App executable was not created: $APP_EXECUTABLE"
[[ -x "$APP_HELPER" ]] || fail "Embedded production helper is missing or not executable: $APP_HELPER"
if [[ "$RETIRE_REMINDER_HELPER" != "1" ]]; then
    [[ -x "$APP_REMINDER_HELPER" ]] || fail "Embedded Reminder helper is missing or not executable: $APP_REMINDER_HELPER"
else
    [[ ! -e "$APP_REMINDER_HELPER" ]] || fail "Retired Reminder helper is unexpectedly bundled: $APP_REMINDER_HELPER"
fi
[[ -x "$APP_MCP_LAUNCHER" ]] || fail "Packaged MCP launcher is missing or not executable: $APP_MCP_LAUNCHER"
[[ -x "$APP_YAN_MCP_LAUNCHER" ]] || fail "Packaged Yan MCP launcher is missing or not executable: $APP_YAN_MCP_LAUNCHER"
[[ "$(lipo -archs "$APP_HELPER")" == "arm64" ]] || fail "Embedded production helper is not arm64."
if [[ "$RETIRE_REMINDER_HELPER" != "1" ]]; then
    [[ "$(lipo -archs "$APP_REMINDER_HELPER")" == "arm64" ]] || fail "Embedded Reminder helper is not arm64."
fi
[[ "$("$APP_HELPER" --version)" == "snotes-gale-production-pull 1" ]] || fail "Embedded production helper version check failed."
if [[ "$RETIRE_REMINDER_HELPER" != "1" ]]; then
    [[ "$("$APP_REMINDER_HELPER" --version)" == "snotes-reminder-helper 1" ]] || fail "Embedded Reminder helper version check failed."
fi
helper_hash="$(shasum -a 256 "$HELPER_EXECUTABLE" | awk '{print $1}')"
embedded_helper_hash="$(shasum -a 256 "$APP_HELPER" | awk '{print $1}')"
[[ "$helper_hash" == "$embedded_helper_hash" ]] || fail "Embedded helper hash differs from the standalone build."
reminder_helper_hash="retired"
if [[ "$RETIRE_REMINDER_HELPER" != "1" ]]; then
    reminder_helper_hash="$(shasum -a 256 "$REMINDER_HELPER_EXECUTABLE" | awk '{print $1}')"
    embedded_reminder_helper_hash="$(shasum -a 256 "$APP_REMINDER_HELPER" | awk '{print $1}')"
    [[ "$reminder_helper_hash" == "$embedded_reminder_helper_hash" ]] || fail "Embedded Reminder helper hash differs from the standalone build."
fi

[[ "$(metadata_value CFBundleName)" == "S-notes" ]] || fail "Incorrect CFBundleName."
[[ "$(metadata_value CFBundleDisplayName)" == "S-notes" ]] || fail "Incorrect CFBundleDisplayName."
[[ "$(metadata_value CFBundleIdentifier)" == "$BUNDLE_IDENTIFIER" ]] || fail "Incorrect CFBundleIdentifier."
[[ "$(metadata_value CFBundleShortVersionString)" == "1.0.0" ]] || fail "Incorrect short version."
[[ "$(metadata_value CFBundleVersion)" == "1" ]] || fail "Incorrect build version."
[[ "$(metadata_value LSMinimumSystemVersion)" == "13.0" ]] || fail "Incorrect minimum macOS version."
[[ "$(metadata_value SNotesSourceCommit)" == "$source_commit" ]] || fail "Incorrect source commit provenance."
[[ "$(metadata_value SNotesSourceDirty)" == "$source_dirty" ]] || fail "Incorrect source dirty provenance."
[[ "$(metadata_value SNotesStep07Candidate)" == "$([[ "$STEP07_CANDIDATE" == "1" ]] && echo true || echo false)" ]] || fail "Incorrect Step 7 candidate marker."

executable_archs="$(lipo -archs "$APP_EXECUTABLE")"
[[ "$executable_archs" == "arm64" ]] || fail "Expected arm64 executable, found: $executable_archs"
[[ -f "$APP_BUNDLE/Contents/Resources/S-notes.icns" ]] || fail "App icon is missing from the bundle."

qt_platform_plugin="$(find "$APP_BUNDLE" -type f -name 'libqcocoa.dylib' -print -quit)"
[[ -n "$qt_platform_plugin" ]] || fail "Qt macOS platform plugin libqcocoa.dylib is missing."

while IFS= read -r -d '' bundle_item; do
    relative_path="${bundle_item#"$APP_BUNDLE"/}"
    if [[ "$relative_path" == "Contents/Resources/certifi/cacert.pem" ]]; then
        embedded_certifi_ca_hash="$(shasum -a 256 "$bundle_item" | awk '{print $1}')"
        [[ "$embedded_certifi_ca_hash" == "$certifi_ca_hash" ]] || fail "Packaged certifi public CA bundle differs from the build environment."
        continue
    fi
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

[[ -f "$APP_BUNDLE/Contents/Resources/web/markdown-editor/dist/index.html" ]] || fail "Bundled Markdown editor missing."
[[ -n "$(find "$APP_BUNDLE" -type f -name QtWebEngineProcess -print -quit)" ]] || fail "QtWebEngineProcess missing."
[[ -n "$(find "$APP_BUNDLE" -type f -name qtwebengine_resources.pak -print -quit)" ]] || fail "QtWebEngine resources missing."

codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

app_size="$(du -sh "$APP_BUNDLE" | awk '{print $1}')"
echo
echo "BUILD SUCCESS"
echo "App: $APP_BUNDLE"
echo "Size: $app_size"
echo "Architecture: $executable_archs"
echo "Helper: $APP_HELPER"
echo "Helper SHA-256: $helper_hash"
echo "Reminder Helper: $APP_REMINDER_HELPER"
echo "Reminder Helper SHA-256: $reminder_helper_hash"
echo "Local MCP Launcher: $APP_MCP_LAUNCHER"
echo "Yan MCP Launcher: $APP_YAN_MCP_LAUNCHER"
echo "Source Commit: $source_commit"
echo "Tracked Source Dirty: $source_dirty"
echo "Qt platform plugin: ${qt_platform_plugin#"$APP_BUNDLE"/}"
echo "Signature: codesign verification passed (ad-hoc)"
echo "The App was not launched or installed."

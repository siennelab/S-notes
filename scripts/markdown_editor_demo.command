#!/bin/bash
# Fixture-only launch: never enter the production bootstrap.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_EXECUTABLE="$ROOT_DIR/dist-d1-candidate/S-notes.app/Contents/MacOS/S-notes"
if [[ ! -x "$APP_EXECUTABLE" ]]; then
    echo "Candidate App missing. Run scripts/build_macos_app.sh --candidate first." >&2
    exit 1
fi
exec "$APP_EXECUTABLE" --markdown-demo

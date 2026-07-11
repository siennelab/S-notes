#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

"$SCRIPT_DIR/scripts/build_macos_app.sh"
status=$?

echo
if [[ $status -eq 0 ]]; then
    echo "S-notes build completed successfully."
else
    echo "S-notes build failed with exit code $status."
fi

read -r -p "Press Return to close this window..." _
exit "$status"

# Release Notes

## Current Internal Version

- Product line: S-notes R1
- Version: `v1.0.0-build.1`
- Platform: macOS arm64 internal
- App version: `1.0.0`
- Build: `1`
- Bundle ID: `de.ke-den.snotes`
- Minimum macOS: macOS 13+
- Signature: ad-hoc

## Current Distribution Boundary

This is an internal local build. The current repository does not provide:

- Developer ID notarization.
- Intel macOS build.
- Universal macOS build.
- Automatic updates.

## Build Entrypoints

`Build-S-notes.command` is the Finder-friendly build launcher for local use.

`scripts/build_macos_app.sh` is the shell packaging workflow used by the launcher
and by direct terminal builds.

Both build paths must continue to keep user data outside the app bundle.

## Formal Gale Background Helper Refresh

Building `dist/S-notes.app` does not install or refresh the independent Gale
background helper under Application Support. A formal closeout that rebuilds
the App must therefore also use the existing operator-controlled helper refresh
step:

1. Compare the embedded helper with the helper path referenced by the loaded
   `com.snotes.gale-production-pull` LaunchAgent.
2. If they differ, atomically install the embedded helper at that existing path
   with owner-only executable permissions.
3. Reload the existing LaunchAgent plist without changing its label, interval,
   data root, or user settings.
4. Verify that both helper SHA-256 values match and run one scheduled one-shot.

The build remains non-installing; no App-startup auto-updater is implied.

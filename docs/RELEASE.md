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

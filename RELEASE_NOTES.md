# v0.1.1 Release Notes

Focused diagnostic release based on the first three Samsung TV Plus / A&E Alaska State Troopers tests.

## Added

- Fully instrumented Continuous TS sessions triggered by VLC/Jellyfin connections.
- `stream-permissive.ts` endpoint.
- `copy-null-permissive`, `normalize-hls-permissive`, and `transcode-hls-permissive` profiles using `-extension_picky 0` before the input.
- Master-to-variant manifest traversal and child playlist snapshots.
- Detection/logging of extensionless media segment URLs.
- Session/profile headers on generated HLS/TS responses.

## Fixed

- Session metadata now distinguishes the configured Jellyfin `play_profile` from the actual diagnostic `session_profile`.
- Continuous TS no longer discards FFmpeg stderr; its diagnostics are written into the session bundle.

## Intentionally unchanged

- No automatic reconnect/restart/recovery behavior.
- No changes to IPTV Merge Manager.
- VAAPI/QSV remain optional Linux hardware paths and are not required for Windows/AMD Docker Desktop testing.

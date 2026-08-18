# Samsung TV Plus Stream Lab v0.2.2 Release Notes

v0.2.2 is the A/V sync tuning release. It preserves the v0.2.1 interleaved-XMLTV fix and makes timestamp correction tunable without rebuilding or waiting for another release.

## A/V sync tuning

- `-dts_delta_threshold` is now persisted as an editable runtime setting.
- New CasaOS/UI controls accept any value from 0.1 to 3600 seconds.
- Quick presets: 1, 10, 30, 60, 90, and 120 seconds.
- The v0.2.2 recommended starting point is 60 seconds.
- Existing v0.2.1 installs still on the untouched 1.0-second default migrate to 60 seconds.
- A saved custom value applies to newly started FFmpeg sessions; active relays keep the value they started with.
- Diagnostic bundles record the effective threshold used for each session.

## New audio-sync profile

- Added `Normalized + Audio Sync · Permissive` (`normalize-hls-sync-permissive`).
- H.264 video remains stream-copy.
- AAC audio is re-encoded with `aresample=async=1:first_pts=0` so audio has a repair path after SSAI timestamp discontinuities.
- Audio-sync AAC bitrate is adjustable from 64–320 kbps (160 kbps default).

## A/V monitoring

- Active HLS sessions periodically probe the newest output segment with ffprobe.
- Diagnostics show current A/V start-time offset and retain the maximum observed absolute offset.
- An `av_sync_warning` event is emitted when the configured warning threshold is crossed.
- Probe interval and warning threshold are adjustable in the same UI.

## Packaging

- GitHub Actions publishes `ghcr.io/kody-r/samsung-tvplus-stream-lab:latest` and `:0.2.2`.
- Existing `/DATA/AppData/samsung-tvplus-stream-lab/data` remains the persistent state volume.

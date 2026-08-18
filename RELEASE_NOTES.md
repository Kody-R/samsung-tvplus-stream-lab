# Samsung TV Plus Stream Lab v0.2.3 Release Notes

v0.2.3 is the **SSAI resilience** release. It keeps the v0.2.2 adjustable DTS/A/V sync controls and adds three larger protections aimed at the Samsung/FAST failure modes observed in real diagnostic bundles.

## Single-rendition pinning

- Stream Lab now resolves HLS master playlists before starting FFmpeg.
- The relay can pin one concrete media playlist instead of giving FFmpeg the whole adaptive master.
- Quality choices: **Best available**, **up to 720p**, **up to 540p**, and **up to 360p**.
- The selected rendition, resolution, bandwidth, master URL, and resolved child URL are recorded in `stream.json` and session events.
- If resolution fails, Stream Lab falls back to the original input URL rather than preventing playback.
- Monitoring now follows the exact selected media playlist plus a lightweight periodic check of the entry master, rather than polling every rendition.

The supplied Samsung diagnostic master contains 360p, 540p, and 720p variants. `Best available` selects the 1280×720 / 3,072,000 bps child directly.

## Automatic relay recovery

- Managed Jellyfin relays can warm-start a replacement FFmpeg worker when:
  - A/V offset exceeds the configured warning threshold for the configured number of probes, or
  - output progress stalls longer than the configured recovery timeout, or
  - the active FFmpeg worker exits unexpectedly.
- The replacement must produce a playlist and at least two segments before Stream Lab switches Jellyfin to it.
- Recovery is rate-limited to avoid restart loops.
- Each replacement is a new **recovery generation** with its own timestamp state and fresh HLS ingest session.
- Diagnostics record recovery requests, successful switches, failed replacement startups, and the reason for each recovery.

## Generation-safe HLS handoff

- Jellyfin still uses the same permanent URL: `/stream/<id>/index.m3u8`.
- Stream Lab rewrites local segment references to include the session generation.
- Old-generation segment requests continue to resolve against the old session files after a replacement takes over.
- Exported local media-sequence numbers are rebased to a monotonically increasing generation-safe range.
- Recovery generations publish an HLS discontinuity sequence so clients can distinguish timeline generations.

This avoids serving `segment_000080.ts` from the wrong worker after an automatic restart.

## SSAI transition capture

- New `#EXT-X-DISCONTINUITY` boundaries on the selected media playlist are detected explicitly.
- When capture is enabled, Stream Lab saves:
  - the transition manifest,
  - boundary metadata / `#EXT-X-ASSET` context,
  - raw MPEG-TS segments surrounding the boundary,
  - response status/content-type/final-URL metadata.
- Captures are stored inside each session under `ssai-captures/` and are included in the normal downloadable diagnostic ZIP.

This gives us a repeatable sample of bad ad transitions without relying only on the FFmpeg text log.

## Cleaner UI

The main navigation is now:

```text
Dashboard | Sources | Channels | Jellyfin | Protection | Diagnostics
```

The new **Protection** page keeps the common controls visible and puts lower-level recovery/audio controls behind an Advanced disclosure.

The three primary toggles are:

- Pin one HLS rendition
- Automatic recovery
- Capture SSAI transitions

DTS presets remain available, including a prominent 120-second test value for the ~90-second discontinuity pattern seen in recent Samsung diagnostics.

## Existing v0.2.2 functionality retained

- Adjustable `-dts_delta_threshold`
- `Normalized + Audio Sync · Permissive`
- AAC `aresample=async=1:first_pts=0`
- Output A/V offset monitoring
- Full M3U/XMLTV source import
- Channel selection and filtered Jellyfin exports
- Interleaved XMLTV compatibility fix
- Extensionless Samsung/Akamai SSAI support through `-extension_picky 0`

## Packaging

GitHub Actions publishes:

```text
ghcr.io/kody-r/samsung-tvplus-stream-lab:latest
ghcr.io/kody-r/samsung-tvplus-stream-lab:0.2.3
```

Persistent CasaOS state remains:

```text
/DATA/AppData/samsung-tvplus-stream-lab/data:/app/data
```

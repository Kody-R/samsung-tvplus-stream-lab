# Samsung TV Plus Stream Lab v0.2.2

Samsung TV Plus Stream Lab is a Docker/CasaOS IPTV stabilization gateway and diagnostic laboratory for Samsung TV Plus, FAST and SSAI HLS streams before they reach Jellyfin.

v0.2.2 includes the v0.2 source-import/channel-selection workflow, the interleaved-XMLTV compatibility fix, and runtime A/V sync tuning for problematic FAST/SSAI streams. Instead of manually adding every channel, give Stream Lab the provider M3U and XMLTV source, choose which channels you want, and Jellyfin receives a filtered playlist whose selected channels are routed through Stream Lab.

## Architecture

```text
Provider full M3U ---------┐
                           ├--> Stream Lab source importer
Provider full XMLTV -------┘          |
                                      v
                              Channel Manager
                              select only wanted
                                      |
                       +--------------+--------------+
                       |                             |
                       v                             v
                 /playlist.m3u                  /guide.xml
                       |                             |
                       +-------------+---------------+
                                     v
                                  Jellyfin
                                     |
                                     v
                           on-demand FFmpeg relay
                                     |
                                     v
                         upstream Samsung/FAST HLS
```

## Source import

Add a source from the **Sources** tab:

- Source ID / name
- Full M3U URL
- Optional full XMLTV or XMLTV.GZ URL
- Default stabilization profile
- Refresh interval (default 6 hours)

Click **Refresh Now**. Stream Lab parses the provider catalog and imports all channels.

New channels are **unselected by default**. This prevents a 900-channel provider playlist from automatically creating a 900-channel Jellyfin lineup.

## Stable refresh behavior

`tvg-id` is used as the stable provider identity whenever available. If an upstream stream URL changes on the next refresh, Stream Lab updates the URL while retaining:

- selected/unselected state
- processing profile
- internal Stream Lab channel ID

Refresh statistics show:

- total channels
- new channels
- missing channels
- changed stream URLs
- XMLTV channel matches

If a previously configured channel disappears, Stream Lab retains its settings and marks it **Missing**. Missing channels are excluded from the current exported M3U but automatically return if the provider restores the same stable channel identity.

## Channel Manager

The Channels tab supports filters for:

- text search
- source
- group
- selected/unselected
- EPG match/missing EPG
- current/missing channels

**Select filtered** and **Deselect filtered** make it easy to include an entire group or search result.

Per-channel playback profiles include:

- Normalized HLS · Permissive (recommended/default)
- Normalized + Audio Sync · Permissive
- Software HLS · Permissive
- Normalized HLS
- Normalized + Audio Sync
- Software HLS

The permissive profiles use FFmpeg `-extension_picky 0`, which was validated against Samsung/Akamai extensionless SSAI segments during the v0.1.x investigation.

## Jellyfin endpoints

Jellyfin only needs two stable URLs:

```text
M3U tuner:
http://YOUR-SERVER-IP:8091/playlist.m3u

XMLTV guide:
http://YOUR-SERVER-IP:8091/guide.xml
```

`/playlist.m3u` exports only selected, present channels. Each exported channel points to:

```text
http://YOUR-SERVER-IP:8091/stream/<internal-id>/index.m3u8
```

The original provider stream URL is not exposed to Jellyfin as the playback target.

`/guide.xml` includes only selected channels and their programme records. XMLTV IDs are remapped to the `tvg-id` exported in the M3U so Jellyfin channel/guide matching stays aligned.

## On-demand resource use

Selecting 300 channels does **not** start 300 FFmpeg processes.

```text
300 channels selected
2 channels being watched
= approximately 2 active HLS relay FFmpeg processes
```

Jellyfin starts a managed relay by requesting its HLS URL. The relay is touched by HLS playlist/segment requests and automatically stops after 30 seconds without a client by default.

Manual diagnostic sessions remain separate and are not subject to the relay idle timeout.

## A/V sync tuning

The **Jellyfin → A/V Sync Tuning** panel exposes:

- DTS delta threshold: 0.1–3600 seconds
- A/V probe interval: 5–300 seconds
- A/V warning threshold: 0.05–60 seconds
- Audio Sync AAC bitrate: 64–320 kbps
- HLS idle timeout: 10–600 seconds

Quick DTS presets are provided for 1, 10, 30, 60, 90 and 120 seconds, while the numeric field accepts custom decimal values. Changes are persisted to `/app/data/streams.json` and affect newly started FFmpeg sessions.

The v0.2.2 starting recommendation is **60 seconds**. Existing v0.2.1 configurations that still contain the untouched hard-coded `1.0` default are migrated to `60.0`; previously customized values are preserved.

Active HLS sessions sample the newest output segment with ffprobe at the configured interval. The Diagnostics table reports the current A/V offset, while `events.jsonl` records `av_sync_sample` and threshold-crossing `av_sync_warning` events.

## Diagnostics

Imported channels still have **Probe** and **Test** actions. Test uses the proven Copy → Null · Permissive path.

Every session can capture:

```text
command.txt
stream.json
ffmpeg.log
progress.jsonl
events.jsonl
manifests/
output/
```

Bundles remain downloadable from the Diagnostics tab.

## Automatic refresh

Each enabled source refreshes on its configured interval (default 6 hours). The scheduler checks periodically and imports new provider state without resetting your selections.

## CasaOS / GHCR

The default Compose file is ready for the public GHCR image:

```yaml
image: ghcr.io/kody-r/samsung-tvplus-stream-lab:latest
pull_policy: always
```

Persistent configuration remains at:

```text
/DATA/AppData/samsung-tvplus-stream-lab/data:/app/data
```

For local development:

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

For compatible Linux hardware acceleration:

```bash
docker compose -f docker-compose.dev.yml -f docker-compose.hw.yml up -d --build
```

## GitHub publishing

`.github/workflows/docker-publish.yml` publishes:

```text
ghcr.io/kody-r/samsung-tvplus-stream-lab:latest
ghcr.io/kody-r/samsung-tvplus-stream-lab:0.2.2
```

Typical release push:

```bash
git add .
git commit -m "Release v0.2.2 - A/V sync tuning"
git push
```

## API additions

```text
GET    /api/status
POST   /api/settings/tuning
GET    /api/channels
POST   /api/channels/select
PATCH  /api/channels/{id}
POST   /api/sources
POST   /api/sources/{id}/refresh
DELETE /api/sources/{id}
GET    /playlist.m3u
GET    /guide.xml
GET    /api/guide/status
POST   /api/guide/refresh
```

## Default settings

```json
{
  "dts_delta_threshold": 60.0,
  "av_sync_probe_seconds": 30,
  "av_sync_warn_seconds": 1.0,
  "audio_sync_bitrate_kbps": 160,
  "guide_cache_seconds": 900,
  "source_refresh_poll_seconds": 60,
  "hls_idle_timeout_seconds": 30
}
```

The DTS delta threshold is editable under **Jellyfin → A/V Sync Tuning** and applies to newly started FFmpeg sessions. The `Normalized + Audio Sync · Permissive` profile keeps video as stream-copy and uses AAC re-encoding plus `aresample=async=1:first_pts=0` for audio correction.

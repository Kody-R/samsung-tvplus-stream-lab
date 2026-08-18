# Samsung TV Plus Stream Lab v0.2.3

Samsung TV Plus Stream Lab is a Docker/CasaOS IPTV stabilization gateway and diagnostic laboratory for Samsung TV Plus, FAST, and SSAI HLS streams before they reach Jellyfin.

v0.2.3 keeps the complete M3U/XMLTV import and channel-selection workflow and adds three larger SSAI protections: **single-rendition pinning**, **automatic relay recovery**, and **raw SSAI transition capture**.

## Architecture

```text
Provider M3U/XMLTV
       |
       v
Channel selection
       |
       v
Stream Lab permanent channel URL
       |
       +--> resolve master -> pin one media rendition
       |
       +--> permissive/timestamp/audio processing
       |
       +--> A/V health + stall monitoring
       |
       +--> warm replacement generation if unhealthy
       |
       v
Jellyfin
```

## Source import and selection

Add a full M3U and optional XMLTV/XMLTV.GZ guide from **Sources**, refresh it, then choose channels on **Channels**. New channels arrive unselected. Provider `tvg-id` is used as the stable identity so selections and processing profiles survive upstream URL changes.

Missing upstream channels remain remembered but are omitted from the current Jellyfin export until they return.

## Jellyfin endpoints

Jellyfin only needs:

```text
M3U tuner:
http://YOUR-SERVER-IP:8091/playlist.m3u

XMLTV guide:
http://YOUR-SERVER-IP:8091/guide.xml
```

Only selected, currently-present channels are exported. Each channel points to Stream Lab:

```text
http://YOUR-SERVER-IP:8091/stream/<internal-id>/index.m3u8
```

## Playback Protection

Open the **Protection** tab for the main runtime safeguards.

### Pin one HLS rendition

Enabled by default. Stream Lab resolves a provider master playlist itself and gives FFmpeg one concrete media child instead of the entire adaptive master.

Available global quality policies:

```text
Best available
Up to 720p
Up to 540p
Up to 360p
```

The recent Samsung diagnostic master contains 360p, 540p, and 720p renditions; Best available selects the 1280×720 child.

### Automatic recovery

Enabled by default for managed Jellyfin relays. Recovery can be triggered by:

- repeated A/V offset failures,
- a prolonged output-progress stall,
- unexpected active-worker exit.

Stream Lab starts a replacement worker first. The replacement must produce a playlist and at least two segments before it becomes the active relay. Each replacement is a new **recovery generation** with fresh FFmpeg/HLS state.

Recovery attempts are rate-limited to avoid loops.

### Generation-safe HLS

The Jellyfin channel URL never changes, but segment URLs inside the local playlist are generation-specific. Old segment requests therefore continue to address the old session files after a replacement has taken over.

Local `#EXT-X-MEDIA-SEQUENCE` values are rebased to a monotonically increasing range, and recovery generations use `#EXT-X-DISCONTINUITY-SEQUENCE` to distinguish the new local timeline.

### Capture SSAI transitions

Enabled by default. When the selected media playlist exposes a new `#EXT-X-DISCONTINUITY`, Stream Lab can save:

```text
ssai-captures/
  capture_001/
    manifest.m3u8
    metadata.json
    segments/
      segment_000.ts
      ...
```

The capture includes the discontinuity/asset context and raw provider segments surrounding the boundary. It is included in the normal session ZIP.

## Processing profiles

Per-channel profiles include:

```text
Normalized + Audio Sync · Permissive
Normalized HLS · Permissive
Software HLS · Permissive
Normalized + Audio Sync
Normalized HLS
Software HLS
```

For new sources, v0.2.3 defaults to **Normalized + Audio Sync · Permissive**. Existing channel profile choices are preserved during upgrade.

Permissive profiles retain the Samsung/Akamai extensionless-segment fix:

```text
-extension_picky 0
```

The audio-sync profile keeps H.264 video stream-copy and re-encodes/resynchronizes AAC audio with:

```text
aresample=async=1:first_pts=0
```

## Timestamp tuning

`-dts_delta_threshold` remains adjustable without rebuilding the app. The Protection tab provides quick values including 30, 60, 120, 180, and 300 seconds, plus a custom numeric field.

Recent Samsung diagnostics showed a repeating ~90.6-second timestamp disagreement after an SSAI boundary, so **120 seconds is a useful controlled test value**. It is not hard-coded; saved custom values persist across restarts.

Advanced controls include:

- A/V warning threshold
- A/V probe interval
- bad A/V probes before recovery
- output-stall recovery time
- Audio Sync AAC bitrate
- HLS idle timeout

## On-demand resource use

Selecting hundreds of channels does not start hundreds of FFmpeg processes. A relay starts only when Jellyfin opens that channel, and the active relay stops after the configured idle timeout when clients leave.

## Diagnostics

The **Diagnostics** tab shows the active rendition, A/V offset, recovery generation, and SSAI capture count.

Session bundles may contain:

```text
command.txt
stream.json
ffmpeg.log
progress.jsonl
events.jsonl
manifests/
output/
ssai-captures/
```

Important v0.2.3 events include:

```text
variant_pinned
ssai_boundary_detected
ssai_capture_complete
input_stream_topology_change
av_sync_warning
auto_recovery_requested
auto_recovery_switch
auto_recovery_failed
recovery_promoted
```

## CasaOS / GHCR

The standard Compose file uses:

```yaml
image: ghcr.io/kody-r/samsung-tvplus-stream-lab:latest
pull_policy: always
```

Persistent state:

```text
/DATA/AppData/samsung-tvplus-stream-lab/data:/app/data
```

The GitHub workflow publishes:

```text
ghcr.io/kody-r/samsung-tvplus-stream-lab:latest
ghcr.io/kody-r/samsung-tvplus-stream-lab:0.2.3
```

Typical update:

```bash
git add .
git commit -m "Release v0.2.3 - SSAI resilience"
git push
```

After GitHub Actions succeeds, update/recreate the CasaOS app so it pulls the new `latest` image.

## Local development

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

Linux hardware override:

```bash
docker compose -f docker-compose.dev.yml -f docker-compose.hw.yml up -d --build
```

## Default protection settings

```json
{
  "variant_pin_enabled": true,
  "variant_quality": "auto",
  "auto_recovery_enabled": true,
  "av_sync_recovery_samples": 2,
  "recovery_stall_seconds": 20,
  "ssai_capture_enabled": true,
  "dts_delta_threshold": 60.0,
  "av_sync_probe_seconds": 30,
  "av_sync_warn_seconds": 1.0,
  "audio_sync_bitrate_kbps": 160,
  "hls_idle_timeout_seconds": 30
}
```

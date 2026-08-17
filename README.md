# Samsung TV Plus Stream Lab v0.1.1

A separate Docker container for reproducing, instrumenting, and testing troublesome Samsung TV Plus / FAST / SSAI streams without adding media-processing code to IPTV Merge Manager.

## What changed in v0.1.1

- **Continuous TS is now fully instrumented.** Every VLC/Jellyfin connection to a TS endpoint creates a normal diagnostic session with `command.txt`, `stream.json`, `ffmpeg.log`, `progress.jsonl`, `events.jsonl`, and manifest snapshots. It appears automatically in **Test Sessions** and can be bundled like any other run.
- Added **Permissive Copy → Null**, **Permissive Normalized HLS**, and **Permissive Software HLS** profiles. These add `-extension_picky 0` before the FFmpeg input so we can directly test Samsung/Akamai SSAI segment URLs that do not have conventional extensions.
- Added a **Permissive Continuous TS** endpoint: `/stream/{id}/stream-permissive.ts`.
- Input diagnostics now follow the master manifest and snapshot **child/variant playlists** (up to two levels deep), where `#EXT-X-DISCONTINUITY`, `#EXT-X-ASSET`, and extensionless SSAI media URLs actually appear.
- `events.jsonl` records playlist type, final URL, HTTP status, content type, discontinuities, asset tags, and detected extensionless segment URLs.
- `stream.json` now records `configured_play_profile` and the actual `session_profile`, eliminating the v0.1.0 ambiguity where a transcode diagnostic could still show the configured `normalize-hls` playback profile.

## Install / upgrade

```bash
unzip samsung-tvplus-stream-lab-v0.1.1.zip
cd samsung-tvplus-stream-lab-v0.1.1
docker compose down
docker compose up -d --build
```

Open:

```text
http://YOUR-SERVER-IP:8091/
```

The default `docker-compose.yml` does **not** map `/dev/dri`, so it works unchanged on Docker Desktop for Windows/AMD. Copy/Null, Normalized HLS, Software HLS, permissive variants, and Continuous TS do not require VAAPI/QSV.

On a compatible Linux host where you want VAAPI/QSV testing, use the included hardware override:

```bash
docker compose -f docker-compose.yml -f docker-compose.hw.yml up -d --build
```

## Test profiles

- **Copy → Null** — demux + stream-copy to null.
- **Copy → Null · Permissive** — same test with `-extension_picky 0`.
- **Normalized HLS** — stream-copy to fresh MPEG-TS HLS; no `-copyts`, no `-re`.
- **Normalized HLS · Permissive** — same output path with permissive HLS input validation.
- **Software HLS** — x264/AAC regeneration with async audio resampling.
- **Software HLS · Permissive** — same transcode with permissive HLS input validation.
- **VAAPI HLS / QSV HLS** — retained for compatible Linux hardware.
- **Continuous TS** — per-viewer MPEG-TS remux, now fully instrumented.
- **Continuous TS · Permissive** — same relay with `-extension_picky 0`.

## Test URLs

For stream ID `1`:

```text
http://YOUR-SERVER-IP:8091/stream/1/index.m3u8
http://YOUR-SERVER-IP:8091/stream/1/stream.ts
http://YOUR-SERVER-IP:8091/stream/1/stream-permissive.ts
```

The HLS URL uses the stream's configured HLS play profile. Each TS URL starts a new per-viewer diagnostic session when a client connects. The response includes `X-Stream-Lab-Session` and `X-Stream-Lab-Profile` headers.

## Diagnostics bundle

Each session stores under `data/sessions/<session-id>/`:

```text
command.txt
stream.json
ffmpeg.log
progress.jsonl
events.jsonl
manifests/
  input-*.m3u8
  variant-*.m3u8
  output-*.m3u8       # HLS output profiles only
output/
  index.m3u8          # HLS output profiles only
  segment_*.ts        # HLS output profiles only
```

Use **Bundle** in the web UI to download a ZIP. Continuous TS does not save the transport stream itself; it records the diagnostics while streaming bytes directly to the viewer.

## Recommended next Samsung test

Using the same direct Samsung/FAST URL that reproduced the v0.1.0 failure:

1. Run **Copy → Null · Permissive** through at least 2–3 ad/SSAI transitions.
2. If it survives, run **Normalized HLS · Permissive**.
3. Open **Continuous TS** in VLC and confirm that a `continuous-ts` session appears in the UI; let it fail naturally and bundle it.
4. Then repeat with **Continuous TS · Permissive** to isolate the effect of `-extension_picky 0` on the exact same relay path.

The lab still performs **no automatic recovery**. A failure is evidence and is intentionally preserved.

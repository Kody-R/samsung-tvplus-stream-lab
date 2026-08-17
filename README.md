# Samsung TV Plus Stream Lab v0.1.2

Samsung TV Plus Stream Lab is a separate Docker service for stabilizing and diagnosing troublesome Samsung TV Plus / FAST / SSAI HLS streams before they reach Jellyfin.

v0.1.2 is a Jellyfin quality-of-life release. It keeps the v0.1.1 permissive HLS and diagnostic behavior, then adds automatically generated M3U and XMLTV endpoints so Jellyfin can point directly at Stream Lab.

## New in v0.1.2

- Global Jellyfin M3U endpoint: `http://SERVER:8091/playlist.m3u`
- Global Jellyfin XMLTV endpoint: `http://SERVER:8091/guide.xml`
- M3U is generated dynamically from all enabled configured streams.
- Per-stream Jellyfin metadata:
  - `tvg-id`
  - channel number
  - group title
  - logo URL
- Optional per-stream XMLTV mapping:
  - XMLTV source URL
  - XMLTV source channel ID
- Multiple XMLTV sources are merged automatically.
- Only programmes for configured Stream Lab channels are copied into the generated guide.
- `.xml` and `.xml.gz` guide sources are supported.
- Guide data is cached for 15 minutes by default to avoid repeatedly downloading provider guides.
- **Refresh Guide Now** invalidates the cache and rebuilds the guide immediately.
- Streams without an XMLTV source still receive a valid `<channel>` entry in `guide.xml`; they simply have no programme entries yet.
- Static JS/CSS URLs now include the Stream Lab version to prevent old browser assets from surviving container upgrades.
- The default HLS profile in the UI is now **Normalized HLS — Permissive**.

## Jellyfin endpoints

Use the CasaOS/server LAN IP rather than `localhost` so the Jellyfin container can reach Stream Lab.

```text
M3U tuner:
http://YOUR-SERVER-IP:8091/playlist.m3u

XMLTV guide:
http://YOUR-SERVER-IP:8091/guide.xml
```

Each generated M3U entry points to:

```text
http://YOUR-SERVER-IP:8091/stream/<id>/index.m3u8
```

The HLS endpoint uses that stream's configured play profile.

## Jellyfin setup

1. Open **Dashboard → Live TV** in Jellyfin.
2. Add an **M3U Tuner** using `http://YOUR-SERVER-IP:8091/playlist.m3u`.
3. Add an **XMLTV** guide data provider using `http://YOUR-SERVER-IP:8091/guide.xml`.
4. Refresh guide data / Live TV channels in Jellyfin.
5. Keep Samsung test streams on **Normalized HLS — Permissive** while validating SSAI transitions.

You no longer need to maintain a separate hand-written M3U file for Stream Lab channels.

## Guide mapping

For each stream, the optional guide fields work like this:

```text
tvg-id
  ID exposed to Jellyfin by Stream Lab.

XMLTV source URL
  Provider guide URL, such as:
  https://provider.example/guide.xml
  https://provider.example/guide.xml.gz

XMLTV channel ID
  The <channel id="..."> value used by that provider's XMLTV file.
  If omitted, Stream Lab assumes it is the same as tvg-id.
```

Example:

```text
Stream ID:          2
Name:               A&E Alaska State Troopers
tvg-id:             stvp-US1900023QQ
Channel number:     139
Group title:        Samsung TV Plus
XMLTV source URL:   https://provider.example/guide.xml
XMLTV channel ID:   provider.ae.alaska
```

Stream Lab publishes the M3U with `tvg-id="stvp-US1900023QQ"`, extracts only programmes belonging to `provider.ae.alaska`, and rewrites those programme records to `channel="stvp-US1900023QQ"`. That keeps the generated M3U and generated XMLTV aligned for Jellyfin.

## Existing v0.1.1 stabilization paths

The v0.1.1 profiles remain intact:

- **Copy → Null**
- **Copy → Null · Permissive** (`-extension_picky 0`)
- **Normalized HLS**
- **Normalized HLS · Permissive**
- **Software HLS**
- **Software HLS · Permissive**
- **VAAPI HLS**
- **QSV HLS**
- **Continuous TS**
- **Continuous TS · Permissive**

The permissive input path is the important Samsung/SSAI workaround discovered during live testing: FFmpeg can accept Samsung/Akamai extensionless segment URLs instead of rejecting them via `allowed_segment_extensions`.

## Individual test URLs

For stream ID `2`:

```text
http://YOUR-SERVER-IP:8091/stream/2/index.m3u8
http://YOUR-SERVER-IP:8091/stream/2/stream.ts
http://YOUR-SERVER-IP:8091/stream/2/stream-permissive.ts
```

## CasaOS / GHCR install

The included default `docker-compose.yml` is intended for the GitHub/GHCR + CasaOS workflow:

```yaml
image: ghcr.io/kody-r/samsung-tvplus-stream-lab:latest
pull_policy: always
```

Persistent application data is stored at:

```text
/DATA/AppData/samsung-tvplus-stream-lab/data
```

The GHCR package must be public for anonymous CasaOS pulls.

For source development instead of GHCR, use:

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

For compatible Linux hardware acceleration during development:

```bash
docker compose -f docker-compose.dev.yml -f docker-compose.hw.yml up -d --build
```

## GitHub publishing

`.github/workflows/docker-publish.yml` publishes:

```text
ghcr.io/kody-r/samsung-tvplus-stream-lab:latest
ghcr.io/kody-r/samsung-tvplus-stream-lab:0.1.2
```

After committing v0.1.2:

```bash
git add .
git commit -m "Release v0.1.2"
git push
```

GitHub Actions builds and publishes the new image. CasaOS can then pull/recreate the app from `:latest`.

## Diagnostics

Every diagnostic session still stores:

```text
command.txt
stream.json
ffmpeg.log
progress.jsonl
events.jsonl
manifests/
output/
```

Continuous TS clients also generate fully instrumented sessions. Use **Bundle** from the web UI after a failure/freeze.

## Guide API

```text
GET  /playlist.m3u
GET  /guide.xml
GET  /api/guide/status
POST /api/guide/refresh
```

Default guide settings in `data/streams.json`:

```json
{
  "guide_cache_seconds": 900,
  "guide_fetch_timeout_seconds": 30
}
```

## Design boundary

Stream Lab still deliberately performs no automatic FFmpeg restart/recovery. Its purpose at this stage is to normalize the Samsung HLS surface for Jellyfin while preserving useful failure evidence rather than hiding it.

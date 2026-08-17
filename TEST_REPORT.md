# Samsung TV Plus Stream Lab v0.1.2 Test Report

Build date: 2026-08-17

## Automated checks

- Python compile: PASS
- Pytest: 10/10 PASS
- JavaScript syntax (`node --check`): PASS
- `docker-compose.yml` YAML parse: PASS
- `docker-compose.dev.yml` YAML parse: PASS
- `docker-compose.hw.yml` YAML parse: PASS
- `casaos/docker-compose.yml` YAML parse: PASS
- GitHub Actions workflow YAML parse: PASS

## M3U generation

PASS:

- Emits `#EXTM3U`.
- Includes configured `tvg-id`, `tvg-name`, `tvg-logo`, `group-title`, and `tvg-chno` metadata when available.
- Points each enabled channel to `/stream/<id>/index.m3u8` on the same host used to request the playlist.

## XMLTV generation

PASS:

- Creates a valid XML declaration and `<tv>` document.
- Creates a channel record even when no XMLTV provider is configured.
- Fetches configured XMLTV sources once per unique source URL per refresh.
- Filters out unrelated channels/programmes.
- Remaps upstream programme `channel=` IDs to Stream Lab's generated `tvg-id`.
- Supports raw `.xml` and gzip-compressed XMLTV payloads.
- Keeps provider fetch errors isolated so one unavailable guide source does not invalidate the entire generated guide.

## End-to-end API smoke test

A temporary HTTP XMLTV provider and v0.1.2 FastAPI server were started locally.

Configured test channel:

```text
A&E Alaska State Troopers
Stream ID: 2
tvg-id: stvp-US1900023QQ
XMLTV source ID: provider.ae
```

Observed:

- `/playlist.m3u` contained exactly the Stream Lab HLS URL.
- `/guide.xml` contained the configured Stream Lab channel.
- The matching Alaska State Troopers programme was included.
- An unrelated provider programme was excluded.
- `/api/guide/status` reported 1 channel / 1 programme / 0 errors.

Result: PASS.

## Live Samsung regression status

v0.1.2 does not modify the FFmpeg stabilization code proven in v0.1.1. The previously successful live `copy-null-permissive` and `normalize-hls-permissive` behavior is preserved unchanged.

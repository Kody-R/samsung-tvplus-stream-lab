# Samsung TV Plus Stream Lab v0.1.1 Test Report

Build date: 2026-08-17

## Static and unit checks

- Python compile: PASS
- JavaScript syntax: PASS
- Docker Compose YAML parse: PASS for default, hardware override, and CasaOS compose files
- Pytest: **6/6 PASS**
- Docker daemon was not available in the build environment, so an actual image build was not performed here.

## Command/profile checks

- Normalized HLS still excludes `-copyts` and `-re`: PASS
- Permissive Copy → Null places `-extension_picky 0` before `-i`: PASS
- Permissive Normalized HLS is recognized as a playable HLS profile: PASS
- Permissive Software HLS is recognized as a playable HLS profile: PASS
- Continuous TS uses MPEG-TS on stdout and FFmpeg progress on stderr: PASS
- Permissive Continuous TS places `-extension_picky 0` before `-i`: PASS

## Extensionless Samsung-style segment reproduction

A synthetic HLS media playlist was built with an extensionless MPEG-TS media URL ending in `/1925`, plus:

```text
#EXT-X-DISCONTINUITY
#EXT-X-ASSET:CAID="smoke-test"
```

Results using the exact same input:

- **Copy → Null**: FFmpeg return code 183 and `not in allowed_segment_extensions` reproduced as expected.
- **Copy → Null · Permissive**: FFmpeg return code 0; the segment was accepted and media progress was recorded.

This validates that the new permissive profile directly changes the failure condition observed in the v0.1.0 Samsung/Akamai sessions.

## Master/variant manifest diagnostics

A synthetic master playlist pointing to a child media playlist was monitored.

PASS:

- master snapshot created
- child/variant snapshot created
- `variant_manifest_change` event recorded
- `extensionless_segments_observed` event recorded
- `#EXT-X-ASSET` count recorded
- `#EXT-X-DISCONTINUITY` count recorded

## Continuous TS end-to-end test

The FastAPI application was launched against the synthetic extensionless HLS input and a client requested:

```text
/stream/1/stream-permissive.ts
```

PASS:

- MPEG-TS response contained 84,036 bytes
- response included `X-Stream-Lab-Session`
- response included `X-Stream-Lab-Profile: continuous-ts-permissive`
- Test Sessions contained the generated `continuous-ts-permissive` run
- session stopped after client EOF/disconnect
- Bundle endpoint successfully generated a ZIP
- ZIP contained `command.txt`, `stream.json`, `ffmpeg.log`, `progress.jsonl`, and `events.jsonl`
- `stream.json` recorded `session_profile=continuous-ts-permissive` separately from `configured_play_profile=normalize-hls`

## Result

v0.1.1 is ready for live Samsung TV Plus testing. The first recommended live run is **Copy → Null · Permissive** using the same A&E Alaska State Troopers URL that failed in the v0.1.0 baseline.

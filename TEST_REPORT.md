# Samsung TV Plus Stream Lab v0.2.3 Test Report

## Automated tests

```text
24 passed
```

Coverage includes:

- M3U source import and filtered Jellyfin exports
- interleaved XMLTV regression
- configurable DTS threshold and persistence
- hybrid H.264-copy/AAC-sync profile
- A/V output sampling
- Samsung-style master-playlist variant parsing
- Best/720p/540p/360p rendition selection
- SSAI/discontinuity boundary parsing
- generation-specific HLS segment rewriting
- monotonically rebased media sequence values
- warm recovery promotion of a ready replacement generation

## Actual Samsung diagnostic-master validation

The master playlist preserved in the supplied v0.2.2 diagnostic bundle was parsed directly:

```text
640x360   bandwidth  921600
960x540   bandwidth 2048000
1280x720  bandwidth 3072000
```

Result:

```text
Best available -> 1280x720 / 3072000
```

An actual captured media playlist containing `#EXT-X-DISCONTINUITY` was also parsed successfully and a concrete transition boundary was identified.

## Local end-to-end rendition smoke test

A local HTTP HLS source was created with a three-rendition master. Stream Lab resolved the master before launching FFmpeg:

```text
requested: http://127.0.0.1:18091/master.m3u8
resolved:  http://127.0.0.1:18091/2.m3u8
quality:   Best available / 720p child
```

`normalize-hls-sync-permissive` then consumed the resolved media playlist and generated local HLS successfully:

```text
FFmpeg return code: 0
local index.m3u8:   generated
local TS output:    generated
```

## Static validation

- Python `compileall`: PASS
- App import / FastAPI route smoke test: PASS
- Browser JavaScript `node --check`: PASS
- `docker-compose.yml`: YAML PASS
- `docker-compose.dev.yml`: YAML PASS
- `docker-compose.hw.yml`: YAML PASS
- `casaos/docker-compose.yml`: YAML PASS
- `.github/workflows/docker-publish.yml`: YAML PASS

## Docker validation

A Docker daemon/CLI is not available in this build environment, so the Docker image itself could not be built here. The included GitHub Actions workflow remains the production image-build validation path.

## Production validation still required

The following require the live CasaOS/Samsung environment:

- GHCR image build and pull
- long-duration 720p-pinned Samsung playback across multiple SSAI transitions
- automatic recovery after a real A/V-desync event
- client behavior during a warm recovery handoff
- captured raw SSAI transition segments from the live provider
- comparison of 60-second vs 120-second DTS thresholds with variant pinning enabled

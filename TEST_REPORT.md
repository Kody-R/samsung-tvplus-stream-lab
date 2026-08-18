# Samsung TV Plus Stream Lab v0.2.2 Test Report

## Automated tests

```text
20 passed
```

Coverage includes the v0.2 source importer/channel selector, v0.2.1 interleaved XMLTV regression, configurable DTS threshold command generation, legacy threshold migration, tuning persistence, the new audio-sync HLS profile, and A/V sync sampling state.

## Static validation

- Python `compileall`: PASS
- Browser JavaScript `node --check`: PASS
- `docker-compose.yml`: YAML PASS
- `docker-compose.dev.yml`: YAML PASS
- `docker-compose.hw.yml`: YAML PASS
- `casaos/docker-compose.yml`: YAML PASS
- `.github/workflows/docker-publish.yml`: YAML PASS

## DTS tuning validation

A simulated v0.2.1 data file containing the old hard-coded `dts_delta_threshold: 1.0` was loaded by v0.2.2:

```text
migrated threshold: 60.0 seconds
```

A custom value was then saved and reloaded:

```text
saved threshold:    90.5 seconds
reloaded threshold: 90.5 seconds
```

The API smoke test successfully saved a custom `120.25` second threshold and returned it through `/api/status`.

## FFmpeg profile smoke test

The new `normalize-hls-sync-permissive` profile was run against a locally generated H.264/AAC HLS input using FFmpeg 7.1.5.

Result:

```text
return code: 0
video codec: stream-copy H.264
audio codec: AAC 160k
audio filter: aresample=async=1:first_pts=0
DTS threshold: 60.0
output HLS: generated successfully
```

The generated command placed both `-dts_delta_threshold` and permissive `-extension_picky 0` before the input.

## A/V sync probe smoke test

The v0.2.2 ffprobe sampler inspected a generated output TS segment and measured:

```text
A/V offset: -0.043 seconds
status: healthy
```

The unit-level sampler test also validates threshold comparison/event state with a synthetic 0.125-second offset.

## Provider compatibility regression

The v0.2.1 XMLTV interleaving fix remains in the test suite. The supplied provider M3U still parses as 514 channels. After removing the browser XML-viewer display sentence and restoring XML escaping in the pasted browser copy solely for structural validation, the supplied guide contains 579 channel IDs and all 514 M3U `tvg-id` values are present.

## Hardware / production validation still required

The build environment does not provide Docker/CasaOS or the user's live Samsung feed. The following remain production tests:

- GHCR Docker image build in GitHub Actions
- CasaOS upgrade using the persistent data directory
- Long-duration Samsung playback at DTS thresholds such as 30, 60, 90, and 120 seconds
- Comparison of `normalize-hls-permissive` versus `normalize-hls-sync-permissive`
- A/V offset behavior through multiple real SSAI ad/discontinuity transitions

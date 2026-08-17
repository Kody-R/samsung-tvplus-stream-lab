# Samsung TV Plus Stream Lab v0.2.0 Test Report

Build date: 2026-08-17

## Static / unit checks

- Python compile: PASS
- Pytest: 14/14 PASS
- JavaScript syntax (`node --check`): PASS
- YAML parse: PASS
  - `docker-compose.yml`
  - `docker-compose.dev.yml`
  - `docker-compose.hw.yml`
  - `casaos/docker-compose.yml`
  - `.github/workflows/docker-publish.yml`

## Source importer tests

PASS:

- Parses full extended M3U metadata (`tvg-id`, logo, group, channel number, name, URL).
- Resolves relative stream URLs against the playlist URL.
- Reads XMLTV channel IDs.
- Builds a normalized XMLTV display-name index for fallback guide matching.
- Exported M3U contains only selected, currently-present channels.
- Missing/unselected channels are excluded.

## End-to-end provider refresh test

A temporary local HTTP provider served a two-channel M3U and XMLTV guide.

Initial import:

- 2 channels imported.
- Both new channels defaulted to unselected.
- 2 XMLTV IDs matched.
- One channel was selected through the API.
- `/playlist.m3u` exported only the selected channel.
- `/guide.xml` retained only the selected channel's programme data.

The provider was then changed:

- selected channel stream URL changed;
- second channel disappeared;
- a new third channel appeared.

Refresh result:

- 2 current upstream channels
- 1 new
- 1 removed/missing
- 1 URL change
- selected channel remained selected
- selected channel adopted the new upstream URL
- missing channel remained remembered and marked Missing
- new channel remained unselected
- exported M3U still contained only the selected current channel

PASS.

## Docker build limitation

The build environment does not provide the Docker CLI/daemon, so an actual container image build could not be executed here. Dockerfile/Compose syntax and the application itself were validated independently. GitHub Actions is configured to perform the GHCR Docker build on push.

# v0.1.2 Release Notes

Quality-of-life release focused on making Samsung TV Plus Stream Lab directly consumable by Jellyfin.

## Added

- Auto-generated `/playlist.m3u` containing every enabled Stream Lab channel.
- Auto-generated `/guide.xml` for Jellyfin XMLTV.
- Per-stream `tvg-id`, logo, group, channel number, XMLTV URL, and XMLTV channel-ID mapping.
- Automatic filtering/merging of multiple XMLTV sources.
- Programme channel-ID remapping so generated M3U and XMLTV always align.
- `.xml.gz` guide input support.
- 15-minute guide cache and **Refresh Guide Now** UI control.
- Jellyfin Integration panel with copyable M3U/XMLTV URLs.
- GitHub Actions GHCR publishing workflow for `latest` and `0.1.2`.
- CasaOS/GHCR-oriented default Compose plus `docker-compose.dev.yml` for source builds.

## Fixed

- Static JS/CSS references now include `?v=0.1.2`, preventing a browser from combining new HTML with cached frontend assets from a previous Stream Lab version.

## Preserved from v0.1.1

- `-extension_picky 0` permissive profiles.
- Instrumented Continuous TS sessions.
- Child/variant HLS manifest capture.
- Extensionless SSAI segment detection.
- Session diagnostic bundles.

## Compatibility

Existing `data/streams.json` files remain valid. Old stream entries do not need migration; missing Jellyfin/XMLTV metadata falls back to the stream ID and an empty programme schedule until the optional guide fields are saved.

# Samsung TV Plus Stream Lab v0.2.0 Release Notes

v0.2.0 turns Stream Lab into a selective IPTV stabilization gateway for Jellyfin.

## Highlights

- Add full M3U + XMLTV provider sources instead of entering channels one at a time.
- Import all channels and preserve provider metadata such as tvg-id, logo, group and channel number.
- Search/filter the imported catalog and select only the channels that should be exported to Jellyfin.
- New channels default to unselected.
- Provider refreshes preserve channel selections and per-channel processing profiles using a stable source key (tvg-id when available).
- Missing upstream channels remain remembered/configured but are flagged Missing and excluded from the current exported M3U until they return.
- Source refresh reports new channels, missing channels, URL changes and EPG matches.
- Automatic source refresh defaults to every 6 hours and is configurable per source.
- Selected channels default to Normalized HLS · Permissive, the Samsung/SSAI stabilization path validated in v0.1.1/v0.1.2 testing.
- Per-channel processing profiles can be changed from the Channel Manager.
- `/playlist.m3u` contains only selected, present channels and points them through Stream Lab rather than to the upstream provider.
- `/guide.xml` filters the source XMLTV down to selected channels while preserving programme metadata and remapping IDs for Jellyfin alignment.
- Large XMLTV documents are filtered with incremental parsing instead of building a complete second XML tree.
- HLS relay FFmpeg processes are started on demand by Jellyfin and automatically stop after an idle timeout (30 seconds by default).
- Manual diagnostic tests still do not auto-stop/restart and retain complete bundle instrumentation.
- New UI tabs: Dashboard, Sources, Channels, Jellyfin and Diagnostics.
- GitHub Actions publishes `ghcr.io/kody-r/samsung-tvplus-stream-lab:latest` and `:0.2.0`.

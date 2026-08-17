# Samsung TV Plus Stream Lab v0.2.1 Release Notes

v0.2.1 is a focused XMLTV compatibility bug-fix release for v0.2.0.

## Fixed

- Fixed source EPG indexing for XMLTV documents that interleave `<channel>` and `<programme>` elements.
- v0.2.0 incorrectly assumed every `<channel>` declaration appeared before the first `<programme>` and stopped indexing at that point.
- Providers that emit one channel followed by its programmes, then the next channel, could therefore report only one EPG match.
- The streaming XMLTV indexer now scans the complete document while clearing programme elements as it goes, keeping memory use bounded.
- Exact `tvg-id` → XMLTV channel ID matching remains authoritative; unique normalized display-name matching remains the fallback.

## Expected result for the supplied Samsung TV Plus provider sample

- M3U channels: 514
- XMLTV channel declarations: 579
- Exact M3U `tvg-id` values present in XMLTV: 514 / 514
- The CasaOS source refresh should therefore report approximately 514 EPG matches, rather than 1.

## Packaging

- GitHub Actions publishes `ghcr.io/kody-r/samsung-tvplus-stream-lab:latest` and `:0.2.1`.
- CasaOS continues to use the persistent `/DATA/AppData/samsung-tvplus-stream-lab/data` volume.
- No data migration is required from v0.2.0.

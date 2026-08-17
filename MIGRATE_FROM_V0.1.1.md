# Upgrade from v0.1.1 to v0.1.2

v0.1.2 is backward compatible with the existing persistent `data/streams.json` volume.

## CasaOS / GHCR

1. Push the v0.1.2 source to GitHub.
2. Wait for the **Build and Publish Docker Image** Action to succeed.
3. Confirm `ghcr.io/kody-r/samsung-tvplus-stream-lab:latest` is publicly pullable.
4. Update/recreate the CasaOS app so it pulls the new `latest` image.
5. Keep `/DATA/AppData/samsung-tvplus-stream-lab/data:/app/data` unchanged.
6. Hard refresh is no longer normally required because v0.1.2 cache-busts its frontend assets automatically.

## Existing streams

Existing streams continue to work immediately. To populate richer Jellyfin metadata/guide data, save each stream with optional:

- `tvg-id`
- channel number
- group title
- logo URL
- XMLTV source URL
- XMLTV channel ID

Then configure Jellyfin once with:

```text
http://SERVER-IP:8091/playlist.m3u
http://SERVER-IP:8091/guide.xml
```

# Migrating from v0.1.2 to v0.2.0

The existing `/app/data/streams.json` file is migrated automatically. Existing manual v0.1.2 streams remain selected and usable.

1. Push the v0.2.0 source to GitHub and wait for the GHCR action to publish `:latest` / `:0.2.0`.
2. Recreate/update the CasaOS app so it pulls the new image.
3. Open Stream Lab and go to **Sources**.
4. Add the provider's full M3U URL and optional XMLTV/XMLTV.GZ URL.
5. Click **Refresh Now** to import the catalog.
6. Go to **Channels**, filter/search, and select the channels you want Jellyfin to receive.
7. Keep Jellyfin pointed at the same stable endpoints:
   - `http://SERVER:8091/playlist.m3u`
   - `http://SERVER:8091/guide.xml`
8. Refresh Jellyfin Live TV channels and guide data.

New imported channels are intentionally unselected. Existing selection/profile decisions survive later provider refreshes.

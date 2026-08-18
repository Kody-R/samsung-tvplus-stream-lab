# Migrating from v0.2.1 to v0.2.2

1. Push the v0.2.2 source to GitHub.
2. Wait for GitHub Actions to publish `ghcr.io/kody-r/samsung-tvplus-stream-lab:latest` and `:0.2.2`.
3. Recreate/update the CasaOS app so it pulls `:latest`.
4. Open **Jellyfin → A/V Sync Tuning** in Stream Lab.
5. The recommended initial DTS delta threshold is **60 seconds**. Save a different value at any time; it is persisted in `/app/data/streams.json` and applies to newly started FFmpeg sessions.

Existing v0.2.1 installations whose threshold is still the old hard-coded 1.0-second default are automatically migrated to 60 seconds. Values that were already manually customized are preserved.

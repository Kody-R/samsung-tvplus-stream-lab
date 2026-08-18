# Migrating from v0.2.2 to v0.2.3

1. Replace the repository files with the v0.2.3 release.
2. Commit and push to GitHub.
3. Wait for GitHub Actions to publish `ghcr.io/kody-r/samsung-tvplus-stream-lab:latest` and `:0.2.3`.
4. Recreate/update the CasaOS app so it pulls the new `latest` image.
5. Keep the existing `/DATA/AppData/samsung-tvplus-stream-lab/data` volume. Source/channel selections and tuning settings are preserved.
6. Open **Protection** after upgrading and review the new defaults:
   - Pin one HLS rendition: enabled
   - Preferred rendition: Best available
   - Automatic recovery: enabled
   - Capture SSAI transitions: enabled

Existing DTS, A/V warning, probe interval, bitrate, channel selection, and per-channel processing-profile settings are not reset.

For the next Samsung test, use `Normalized + Audio Sync · Permissive`. Because recent diagnostics contained a recurring ~90.6-second discontinuity, 120 seconds is a useful controlled DTS test value; variant pinning and recovery can be tested independently of that number.

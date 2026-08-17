# Migrating from v0.2.0 to v0.2.1

No configuration or persistent-data migration is required.

1. Push the v0.2.1 source to GitHub.
2. Wait for GitHub Actions to publish `ghcr.io/kody-r/samsung-tvplus-stream-lab:latest` and `:0.2.1`.
3. Recreate/update the CasaOS app so it pulls the new image.
4. Open Stream Lab → Sources and click **Refresh Now** for the affected source.
5. Confirm the EPG match count rises from the incorrect value of 1 to the expected full match count.

Selections, per-channel profiles, and persistent data under `/DATA/AppData/samsung-tvplus-stream-lab/data` are retained.

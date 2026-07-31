# Game Observatory deployment

This deployment keeps the canonical SQLite/artifact tree on a host volume and puts the
standalone FastAPI service behind an Nginx read proxy with write-rate limiting.

1. Create author/reviewer/admin tokens without committing them:

   ```powershell
   $env:OMNI_GAME_OBSERVATORY_TOKENS='{"author-token":"author","review-token":"reviewer","admin-token":"admin"}'
   ```

2. From this directory run `docker compose up --build -d`.
3. Check `http://127.0.0.1:8222/api/game-observatory/health` and the public site at
   `http://127.0.0.1:8222/game-observatory/`.
4. Back up and run a recovery drill before upgrading:

   ```powershell
   python -m omnicompany.packages.domains.game_observatory.cli backup
   python -m omnicompany.packages.domains.game_observatory.cli recovery-drill --destination <backup-dir>
   ```

TLS/CDN and public DNS remain deployment-environment concerns. Do not expose editor tokens through
CDN caches; cache only anonymous GET responses and preserve same-origin API routing.

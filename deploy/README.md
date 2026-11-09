# Deploy it: one VPS, the team only

PostgreSQL and the API run from `deploy/compose.yaml` on a VPS. Teammates reach the API over
Tailscale; nothing listens on the public interface.

- `postgres` publishes no port and sits on an internal Docker network. Its data is in the named volume
  `pgdata`.
- `api` is published only as `127.0.0.1:8000:8000`. Uvicorn binds `0.0.0.0` inside the container, which
  is why that publish must stay on loopback.
- Tailscale is installed on the host, not in a container. `tailscale serve` gives teammates HTTPS on the
  tailnet and forwards to `127.0.0.1:8000`.
- **Keep the VPS firewall closed to 5432 and 8000.** A port Docker publishes on `0.0.0.0` goes around
  `ufw`'s rules, so the safety here is that one port is bound to loopback and the other is not published
  at all. Do not edit `compose.yaml` to publish either, and open nothing but SSH (and what Tailscale
  needs) in the provider's firewall.
- Secrets live in `deploy/.env` (mode 600, git-ignored, made by `setup.sh`), with no defaults: compose
  refuses to start without them. The API refuses to start in production (`BIOSENTRY_ENV=production`,
  set in `compose.yaml`) on the dev database password or an ingest key under 32 characters.

## The order

1. **SSH in** to the VPS as a normal user with `sudo`.
2. **Install Docker Engine and the compose plugin** from Docker's own instructions for your distribution
   (<https://docs.docker.com/engine/install/>), and add yourself to the `docker` group (log out and in).
   Check: `docker compose version`.
3. **Install Tailscale on the host** (<https://tailscale.com/download/linux>), then
   `sudo tailscale up` and open the login URL it prints in your own browser, signing in yourself.
   In the Tailscale admin console, turn on MagicDNS and HTTPS certificates (DNS page); this is needed
   for `tailscale serve --https`. Share the node with the other two teammates (or add them to the tailnet).
4. **Clone** the repository: `git clone <the repository URL> biosentry && cd biosentry`.
5. **Run setup**: `deploy/setup.sh`. It checks Docker, compose, Tailscale and openssl are present, makes
   `deploy/.env` (random Postgres password and ingest key, never printed) if it does not exist, builds
   the image and starts the stack, waiting until it is healthy. Re-running it is safe: it keeps the
   existing `.env`. Do not delete `deploy/.env` while the volume exists; the volume remembers its password.
6. **Publish to the tailnet**: `sudo tailscale serve --bg --https=443 http://127.0.0.1:8000`.
   The API is then at `https://<this-host>.<your-tailnet>.ts.net`; `tailscale serve status` shows it.
   (Only tailnet devices can reach it: this is `serve`, not `funnel`. Never run `tailscale funnel`.)
7. **Set each clinician's passphrase** inside the api container (at least 12 characters; it prompts
   twice and the passphrase is never shown or logged). The seed gives a production database no
   passphrases, so until you do this nobody can sign in:

   ```sh
   docker compose -f deploy/compose.yaml exec api python -m biosentry.db.passphrase rn-manalo
   ```

   Repeat for `rn-delacruz`, `rn-reyes`, `md-santos`, `md-aquino`. Give each person theirs privately.
   (Piped input works too, for a script: `printf '%s\n' "$PASSPHRASE" | docker compose -f
   deploy/compose.yaml exec -T api python -m biosentry.db.passphrase rn-manalo`.)
8. **Give each teammate the ingest key privately**, for the ward sim. Read it on the VPS, over SSH, with
   `grep BIOSENTRY_INGEST_KEY deploy/.env`, and send it through the team password manager (never chat,
   email or the repository). The sim posts to `https://<this-host>.<your-tailnet>.ts.net/sim/runs` with
   the header `X-Ingest-Key`. To rotate it, edit `deploy/.env` and run `deploy/setup.sh` again.
9. **Build the dashboard** against the API with `VITE_API_URL=https://<this-host>.<your-tailnet>.ts.net`.
   If it is served from any origin other than `localhost:5173`, put that origin in
   `BIOSENTRY_CORS_ORIGINS` in `deploy/.env` (comma-separated) and run `deploy/setup.sh` again.

Updating: `git pull && deploy/setup.sh`. Logs: `docker compose -f deploy/compose.yaml logs -f api`.

## Backups

A VPS has no managed backups, so take them. `deploy/backup.sh` runs `pg_dump` (custom format) inside the
postgres container into `~/biosentry-backups` (or `BACKUP_DIR`; the directory is made mode 700, the files
600) as `biosentry-<UTC timestamp>.dump`, and keeps the newest 14 (`KEEP` changes that). One cron line,
daily at 03:00 (`crontab -e`; use the real path of your clone):

```cron
0 3 * * * /home/USER/biosentry/deploy/backup.sh >> /home/USER/biosentry-backups/backup.log 2>&1
```

The dumps are on the same disk as the database, so copy them somewhere else too (for example
`rsync` the directory to another machine you own, over SSH or the tailnet).

**Restore in place** (stops the API so nothing is writing). This is destructive: `--clean` drops the
tables and loads the backup's copy of everything, **including the audit log, so audit rows written after
the backup was taken are lost**, along with every other change since:

```sh
docker compose -f deploy/compose.yaml stop api
docker compose -f deploy/compose.yaml exec -T postgres \
  pg_restore -U biosentry -d biosentry --clean --if-exists --no-owner \
  < ~/biosentry-backups/biosentry-20261109T030000Z.dump
docker compose -f deploy/compose.yaml start api
```

The restore brings the audit log's append-only triggers back with it.

**To keep the current database for comparison, restore into a fresh one instead** (the live database and
its audit log are not touched; point nothing at the copy until you have looked at it):

```sh
docker compose -f deploy/compose.yaml exec -T postgres createdb -U biosentry biosentry_restored
docker compose -f deploy/compose.yaml exec -T postgres \
  pg_restore -U biosentry -d biosentry_restored --no-owner \
  < ~/biosentry-backups/biosentry-20261109T030000Z.dump
docker compose -f deploy/compose.yaml exec postgres psql -U biosentry -d biosentry_restored
```

When you are done with it: `... exec -T postgres dropdb -U biosentry biosentry_restored`.

## Trying the stack on another machine

The same files work anywhere with Docker: `COMPOSE_PROJECT_NAME=bs-try deploy/setup.sh` needs a
`tailscale` on the PATH (any stand-in will do for a test), and `docker compose -f deploy/compose.yaml`
with that project name, then `down -v` removes it all. The api is on that machine's `127.0.0.1:8000`, so
stop anything already using that port first.

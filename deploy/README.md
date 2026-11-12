# Deploy it: one VPS, the team only

PostgreSQL, the API and the ward simulator run from `deploy/compose.yaml` on a VPS. Teammates reach
the API over Tailscale; nothing listens on the public interface.

- `postgres` publishes no port and sits on an internal Docker network. Its data is in the named volume
  `pgdata`.
- `api` is published only as `127.0.0.1:8000:8000`. Uvicorn binds `0.0.0.0` inside the container, which
  is why that publish must stay on loopback.
- `sim` is the ward simulator, running headless: **the ward now runs on the server, continuously**, and
  the dashboard and the browser simulator page show it. It is the `biosentry-ward-sim` repository,
  built from a sibling checkout that `setup.sh` clones (and, on a re-run, fast-forwards with
  `git pull --ff-only`) into `SIM_DIR`, `../../biosentry-ward-sim` relative to `deploy/` by default. It
  publishes no port and is on the default network only, so it reaches the API at `http://api:8000` and
  not the database. It writes through `/sim/*` with the ingest key (the same `BIOSENTRY_INGEST_KEY` the
  API holds, which compose refuses to start without) and keeps its state in the named volume
  `simstate`, mounted at `/state`. `WARD_SPEED` is the speed a new ward starts at, in simulated seconds per
  second: 1, 10 or 60, 1 unless set (the runner exits on anything else). A ward resumed from its saved
  state keeps its saved speed, and a clinician who is an md or the charge nurse changes it from the viewer
  afterwards.
- There is one ward. While the runner is live, a browser simulator started with the ingest key is
  **refused with 409** ("Another ward simulation is live"): use the ward on the server, not a second one.
  To run one from a browser anyway, stop the runner (`docker compose -f deploy/compose.yaml stop sim`);
  its run goes stale after 30 seconds without a batch.
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
3. **Install Tailscale on the host** (<https://tailscale.com/download/linux>). On a host whose firewall
   drops forwarded traffic and all inbound but SSH, Tailscale must run in userspace networking, because
   kernel-mode `tailscale0` inbound connections would be dropped and containers have no outbound network
   (this is why the image build uses the host network, see `compose.yaml`). Set
   `FLAGS="--tun=userspace-networking"` in `/etc/default/tailscaled`, run
   `sudo systemctl restart tailscaled`, and only then
   `sudo tailscale up --hostname=biosentry-api --accept-dns=false`. Open the login URL it prints in your
   own browser, signing in yourself. Do not change the firewall.
   In the Tailscale admin console, turn on MagicDNS and HTTPS certificates (DNS page); this is needed
   for `tailscale serve --https`. Share the node with the other two teammates (or add them to the tailnet).
4. **Clone** the repository: `git clone <the repository URL> biosentry && cd biosentry`.
5. **Run setup**: `deploy/setup.sh`. It checks Docker, compose, git, Tailscale and openssl are present,
   makes `deploy/.env` (random Postgres password and ingest key, never printed) if it does not exist,
   clones the simulator into `SIM_DIR` (or updates it), builds the images and starts the stack, waiting
   until it is healthy. Re-running it is safe: it keeps the existing `.env`. Do not delete `deploy/.env`
   while the volume exists; the volume remembers its password. To keep the simulator checkout somewhere
   else, set `SIM_DIR` in the environment of the command (`SIM_DIR=/srv/ward-sim deploy/setup.sh`), not in
   `.env`, and set it the same way for any `docker compose` command that builds. The `biosentry-ward-sim`
   repository's `main` branch must already contain the runner's `Dockerfile` before you run setup, or the
   `sim` build fails. Likewise `WARD_SPEED`
   can be set in `deploy/.env` or the environment.
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

Updating: `git pull && deploy/setup.sh` (it updates the simulator checkout too). Logs:
`docker compose -f deploy/compose.yaml logs -f api` (or `sim`).

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

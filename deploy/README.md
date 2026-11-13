# Deploy it: one server, the team only

PostgreSQL, the API, the ward simulator and the two browser apps run from `deploy/compose.yaml` on one
Linux server (a Huawei Cloud Flexus L or ECS, see "On Huawei Cloud" below, or any VPS). Teammates open
**one address** over Tailscale; none of the stack's ports listen on the public interface, and a stranger
on the internet can reach no page and no data.

| The team opens | and gets |
| --- | --- |
| `https://<this-host>.<your-tailnet>.ts.net/` | the dashboard |
| `https://<this-host>.<your-tailnet>.ts.net/simulator/` | the full ward simulator: the ward the server is running |
| `https://<this-host>.<your-tailnet>.ts.net/api/...` | the API, which the two pages call themselves |

The public demos on Cloudflare Pages (`biosentry-dashboard.pages.dev`, `biosentry-ward-sim.pages.dev`) are
not part of this and do not change: they hold sample data only and no API address. Do not point them at
this server (and see "A page served from somewhere else" for why a public page cannot call it anyway).

- `postgres` publishes no port and sits on an internal Docker network. Its data is in the named volume
  `pgdata`.
- `api` is published only as `127.0.0.1:8000:8000`. Uvicorn binds `0.0.0.0` inside the container, which
  is why that publish must stay on loopback.
- `web` is the address the team opens, published only as `127.0.0.1:8080:8080`: a small web server
  (`web.Dockerfile`, `Caddyfile`) with the dashboard at `/`, the full ward simulator at `/simulator/` and
  the API passed through at `/api`. Compose builds both pages on the server, from this repository's
  `frontend/` and from the simulator checkout, already set to call `/api` on their own address. So there
  is no API address to configure, no cross-origin request, and no build to upload anywhere. It holds no
  secret, has no route to the database, and answers 404 for `/api/sim/*`: the ingest door cannot be
  reached from the team's address at all.
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
- `lab` is the lab worker: it runs the simulated compare runs a physician or the charge nurse asks for
  on the dashboard's Simulation lab page (without it a request waits, then fails). It is the same image
  as `sim` with another command (`node dist/lab/lab-worker.js`). It claims a request from the API with
  the ingest key, runs the simulator's compare, and posts the result back. It publishes no port and
  sits on an internal network (`labnet`) with the API alone (and, as on any Docker network, the host's
  own address on that bridge: keep the host's services off it, see the firewall note below): no route out
  and none to the database. Its
  filesystem is read-only apart from an in-memory `/tmp` capped at 64 MB (Docker's own `/dev/shm` is
  writable too; the worker does not use it), all capabilities are dropped, and it keeps
  nothing. One run at a time, for up to 15 minutes; a run uses about one CPU (the service is capped at
  two) and 1 GB of memory at most. The largest request, a 30-day hospital run, took about 2 minutes and
  200 MB on a laptop. It refuses to start if a proxy
  variable (`HTTP_PROXY` and the like) is set, so do not give the stack one.
- There is one ward. While the runner is live, a browser simulator started with the ingest key is
  **refused with 409** ("Another ward simulation is live"): use the ward on the server, not a second one.
  To run one from a browser anyway, stop the runner (`docker compose -f deploy/compose.yaml stop sim`);
  its run goes stale after 30 seconds without a batch.
- Tailscale is installed on the host, not in a container. `tailscale serve` gives teammates HTTPS on the
  tailnet and forwards to `127.0.0.1:8080`.
- **Keep the server's firewall closed to 5432, 8000 and 8080.** A port Docker publishes on `0.0.0.0` goes
  around `ufw`'s rules, so the safety here is that two ports are bound to loopback and the third is not
  published at all. Do not edit `compose.yaml` to publish any of them, and open nothing but SSH in the
  provider's firewall (Tailscale needs no inbound port).
- A clinician still signs in with their own passphrase, on both pages. The API answers each one only for
  their own patients and writes an audit row for every view. Tailscale decides who can reach the address;
  the passphrase decides whose patients they see.
- Secrets live in `deploy/.env` (mode 600, git-ignored, made by `setup.sh`), with no defaults: compose
  refuses to start without them. The API refuses to start in production (`BIOSENTRY_ENV=production`,
  set in `compose.yaml`) on the dev database password or an ingest key under 32 characters.

## The order

1. **SSH in** to the server as a normal user with `sudo`.
2. **Install Docker Engine and the compose plugin** from Docker's own instructions for your distribution
   (<https://docs.docker.com/engine/install/>), and add yourself to the `docker` group (log out and in).
   Check: `docker compose version`.
3. **Install Tailscale on the host** (<https://tailscale.com/download/linux>). On a host whose firewall
   drops forwarded traffic and all inbound but SSH, Tailscale must run in userspace networking, because
   kernel-mode `tailscale0` inbound connections would be dropped and containers have no outbound network
   (this is why the image build uses the host network, see `compose.yaml`). Set
   `FLAGS="--tun=userspace-networking"` in `/etc/default/tailscaled`, run
   `sudo systemctl restart tailscaled`, and only then
   `sudo tailscale up --hostname=biosentry --accept-dns=false`. Open the login URL it prints in your
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
6. **Publish to the tailnet**: `sudo tailscale serve --bg --https=443 http://127.0.0.1:8080`.
   The dashboard is then at `https://<this-host>.<your-tailnet>.ts.net/` and the full ward simulator at
   `.../simulator/`; `tailscale serve status` shows the address, and should list this one line only (if it
   also lists an older one for port 8000, clear it first with `sudo tailscale serve reset`). `--bg` keeps
   it on across reboots.
   (Only tailnet devices can reach it: this is `serve`, not `funnel`. Never run `tailscale funnel`.)
   Each teammate installs Tailscale on their own laptop or phone and joins the tailnet; nothing else.
7. **Set each clinician's passphrase** inside the api container (at least 12 characters; it prompts
   twice and the passphrase is never shown or logged). The seed gives a production database no
   passphrases, so until you do this nobody can sign in:

   ```sh
   docker compose -f deploy/compose.yaml exec api python -m biosentry.db.passphrase rn-manalo
   ```

   Repeat for `rn-delacruz`, `rn-reyes`, `md-santos`, `md-aquino`. Give each person theirs privately.
   Running it again for an id replaces that passphrase and signs that clinician out everywhere.
   (Piped input works too, for a script: `printf '%s\n' "$PASSPHRASE" | docker compose -f
   deploy/compose.yaml exec -T api python -m biosentry.db.passphrase rn-manalo`.)
8. **Keep the ingest key on the server.** Nobody on the team needs it: the ward runs in the `sim`
   container and the lab worker in `lab`, which get the key from `deploy/.env`, and the two pages never
   use it. It is not in any page
   or build. To rotate it, edit `deploy/.env` and run `deploy/setup.sh` again. (Only someone running a
   second ward from their own machine would need it, and while the runner is live that is refused with
   409 anyway.)
9. **Open the address** from a teammate's device that is on the tailnet, and sign in.

Updating: `git pull && deploy/setup.sh` (it updates the simulator checkout too). Logs:
`docker compose -f deploy/compose.yaml logs -f api` (or `sim`, `lab`, or `web`). A change to the dashboard or the
simulator reaches the team the same way: that command rebuilds the pages on the server.

## Backups

A plain server has no managed backups, so take them. `deploy/backup.sh` runs `pg_dump` (custom format) inside the
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

## A page served from somewhere else

You do not need this: the `web` service serves both pages. If you ever build a page yourself and serve it
from another origin, build it with the API's address (`VITE_API_URL` for the dashboard,
`VITE_VIEW_API_URL` for the simulator page; never put the ingest key in a build, a `VITE_` value ends up
in public JavaScript) and list that page's exact origin in `BIOSENTRY_CORS_ORIGINS` in `deploy/.env`
(comma-separated), then run `deploy/setup.sh` again. An origin that is not listed gets no CORS header and
the browser refuses the answer.

Do not do this from a public site such as `*.pages.dev`. The page itself would be public, and Chrome
(142 and later) treats a call from a public page to a tailnet address as a local-network request: every
user gets a permission prompt, and the call fails if they decline.

## On Huawei Cloud

The same order works on one Huawei Cloud server. The cheapest fit is a **Flexus L** instance in
**AP-Manila** or CN-Hong Kong, with a plain Ubuntu or Debian image: its disk, its public address and up to
3 TB of traffic a month are in one monthly price (USD 9 for 2 vCPUs and 2 GiB, USD 19 for 4 GiB, checked
2026-11-13). The server builds the API image and the two pages itself, which has not been tried on 2 GiB;
pick 4 GiB at purchase if you would rather not find out, since upgrading a running Flexus L is not checked
here. A classic ECS (for example `s6.medium.2`) works the same way but is billed piece by piece. What
differs from a plain VPS:

- The server needs its public address (EIP) only to reach **out**: Docker's image downloads and Tailscale
  both connect outward. Flexus L comes with one. An ECS without one is expected to have no internet access
  (not checked here; a NAT gateway is the costlier alternative), so add an EIP billed by traffic. A
  pay-per-use EIP keeps billing while the server is stopped, until you release it.
- In the security group, first add (or edit) one inbound rule: SSH (22) from your own IP address
  (`x.x.x.x/32`), and check that you can still SSH in. Then delete any other inbound rule you find there
  (the console may add rules for 80, 443 or ping), unless another server on the same account needs it: on
  Flexus L, all of an account's instances in one region share one security group. Add nothing for 8000,
  8080 or 5432: nothing here needs an inbound port, and Tailscale needs none either. Leave the default
  outbound rule (allow all). If your home IP changes, SSH stops working until you edit the rule (or use
  the tailnet, below).
- Once Tailscale is up you can SSH over the tailnet instead, and then remove the SSH rule. Do it in this
  order: first check that `ssh <you>@<this-host>` works from your laptop over the tailnet; then set a
  login password for the server in the console (reset password) and check that the console's remote login
  (VNC) lets you in; only then delete the rule. Huawei documents VNC login for ECS as needing no public
  address; it is not checked here for Flexus L. If either check fails, keep the SSH rule.
- Use Tailscale on this server only to reach this server. Do not make it an exit node or a subnet router:
  Huawei's ECS service statement forbids using a server for "cross-border VPN" or "traffic transit", and
  either could look like that (our reading, not a Huawei ruling).
- Buy by the month with auto-renew off, and renew by hand before the month ends; set a budget alert in
  Cost Center too (it only warns; Huawei documents no spending cap). An expired Flexus L is frozen and
  later deleted with its data, which cannot be restored: before it lapses, run `deploy/backup.sh` and copy
  the dump off the server.
- Huawei's managed database (RDS for PostgreSQL, which offers version 18) is not needed: PostgreSQL runs
  in this stack. The API reads its database address from `BIOSENTRY_DATABASE_URL`, set in
  `deploy/compose.yaml`; moving to RDS would mean changing that line and has not been done or tested here
  (RDS is reached over a private network from a server in the same VPC, and whether a Flexus L can be is
  not checked).

## Trying the stack on another machine

The same files work anywhere with Docker, without Tailscale and without `setup.sh` (which insists on
Tailscale). `API_PORT` and `WEB_PORT` move the two loopback ports when 8000 or 8080 is taken, and a
project name keeps it apart from anything else on the machine. With `deploy/.env` holding
`POSTGRES_PASSWORD` and `BIOSENTRY_INGEST_KEY` (make them as `setup.sh` does, with `openssl rand -hex`),
and the simulator checked out beside this repository or at `SIM_DIR`:

```sh
API_PORT=18000 WEB_PORT=18080 docker compose -p bs-try -f deploy/compose.yaml up -d --build --wait
```

Then set a passphrase for each clinician (step 7, with `-p bs-try`) and open
`http://127.0.0.1:18080/`. The stack runs in production mode here too, so the public dev passphrase
does not work. `docker compose -p bs-try -f deploy/compose.yaml down -v` removes it all.

#!/bin/sh
# Bring the team stack up on the server (deploy/README.md has the whole order). Safe to re-run.
#
#   checks Docker (with compose), git, Tailscale and openssl are installed
#   -> makes deploy/.env, mode 600, if it does not exist (random Postgres password and ingest key,
#      never printed; an existing one is kept, and its mode put right)
#   -> clones the ward simulator beside this repository (SIM_DIR), or updates it if it is there
#   -> builds the images (the API, the ward runner and lab worker, and the web server with the two pages) and starts
#      the stack, waiting until it is healthy.
#   -> public mode, only when PUBLIC_HOST is set in deploy/.env (deploy/README.md, "Public mode"): it
#      adds compose.public.yaml. This script never opens the address itself: it prints the command.
#
# Do not delete deploy/.env while the database volume exists: the volume keeps the password it was
# first created with.
set -eu
cd "$(dirname "$0")"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is not installed: $2 (deploy/README.md, steps 2 and 3)" >&2
    exit 1
  fi
}
need docker "install Docker Engine with the compose plugin"
docker compose version >/dev/null 2>&1 || {
  echo "the docker compose plugin is not installed (deploy/README.md, step 2)" >&2
  exit 1
}
need git "install git"
need tailscale "install Tailscale on this host, not in a container"
need openssl "install openssl"

if [ -e .env ]; then
  chmod 600 .env
else
  (
    umask 077
    {
      printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)"
      printf 'BIOSENTRY_INGEST_KEY=%s\n' "$(openssl rand -hex 32)"
      printf 'BIOSENTRY_CORS_ORIGINS=\n'
    } > .env
  )
  chmod 600 .env
  echo "created deploy/.env (mode 600): the secrets are in it and nowhere else"
fi

# The ward simulator's code: compose builds the `sim` service from this checkout (compose.yaml).
# Set SIM_DIR in the environment of this script to keep it elsewhere; it is passed on to compose.
SIM_DIR="${SIM_DIR:-../../biosentry-ward-sim}"
export SIM_DIR
if [ -e "$SIM_DIR" ]; then
  git -C "$SIM_DIR" pull --ff-only
else
  git clone https://github.com/kitchen-station/biosentry-ward-sim.git "$SIM_DIR"
fi

# Public mode is off unless PUBLIC_HOST is set in .env: the server's own name on the tailnet, which
# `tailscale funnel` opens to the internet. Checked here because it is printed into commands below.
public_host=$(sed -n 's/^PUBLIC_HOST=//p' .env | tail -n 1)
if [ -z "$public_host" ]; then
  docker compose up -d --build --wait
  echo "up: the team's address answers on this host at 127.0.0.1:8080 (the API alone at 127.0.0.1:8000) and nowhere else."
  echo "next: sudo tailscale serve --bg --https=443 http://127.0.0.1:8080, then set each clinician's passphrase."
  exit 0
fi
case "$public_host" in
  *[!a-z0-9.-]* | .* | -* | *..*) public_host="" ;;
  *.ts.net) ;;
  *) public_host="" ;;
esac
if [ -z "$public_host" ]; then
  echo "PUBLIC_HOST in deploy/.env must be this server's tailnet name, like server.tail1234.ts.net (lower case, no https://)" >&2
  exit 1
fi
# Anyone on the internet could then watch the ward without signing in: never both.
if grep -Eiq '^BIOSENTRY_OPEN_WARD_VIEW=[[:space:]"]*true[[:space:]"]*$' .env; then
  echo "PUBLIC_HOST and BIOSENTRY_OPEN_WARD_VIEW=true are both set in deploy/.env: remove one (a public address never shows the ward without a sign-in)" >&2
  exit 1
fi
docker compose -f compose.yaml -f compose.public.yaml up -d --build --wait
echo "up, set for a public address. The stack itself still answers on this host at 127.0.0.1:8080 and nowhere else."
echo "to open it to the internet: sudo tailscale funnel --bg --https=443 http://127.0.0.1:8080"
echo "to close it again:          sudo tailscale funnel reset && sudo tailscale serve --bg --https=443 http://127.0.0.1:8080"
echo "the two public sites build with: VITE_API_URL=https://$public_host/api (dashboard) and VITE_VIEW_API_URL=https://$public_host/api (simulator)"

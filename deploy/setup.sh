#!/bin/sh
# Bring the team stack up on the VPS (deploy/README.md has the whole order). Safe to re-run.
#
#   checks Docker (with compose), git, Tailscale and openssl are installed
#   -> makes deploy/.env, mode 600, if it does not exist (random Postgres password and ingest key,
#      never printed; an existing one is kept, and its mode put right)
#   -> clones the ward simulator beside this repository (SIM_DIR), or updates it if it is there
#   -> builds the images and starts the stack, waiting until it is healthy.
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

docker compose up -d --build --wait
echo "up: the API answers on this host at 127.0.0.1:8000 and nowhere else."
echo "next: tailscale serve --bg --https=443 http://127.0.0.1:8000, then set each clinician's passphrase."

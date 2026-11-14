# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
#
# The team's one address: the dashboard at /, the full ward simulator at /simulator/ and the API at /api, all from
# one small web server (deploy/Caddyfile). Built by deploy/compose.yaml, which hands it the two apps'
# code as the named contexts `frontend` (this repository's frontend/) and `sim` (the biosentry-ward-sim
# checkout, SIM_DIR). Nothing secret goes in: the only setting baked into the pages is the path /api.
#
# To upgrade a pin, copy the new digest (`docker buildx imagetools inspect <tag>`).

# The same node image as the simulator's own Dockerfile.
ARG NODE=node:22-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee

# --- the dashboard, built to read the API at /api on the address it is served from
FROM ${NODE} AS dashboard
WORKDIR /app
COPY --from=frontend package.json package-lock.json ./
RUN npm ci
COPY --from=frontend index.html tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts ./
COPY --from=frontend public ./public
COPY --from=frontend src ./src
RUN VITE_API_URL=/api npx vite build

# --- the full ward simulator page: it draws the ward the runner is running (the simulator's viewer
# build, VITE_VIEW_API_URL), under /simulator/
FROM ${NODE} AS simulator
WORKDIR /app
COPY --from=sim package.json package-lock.json ./
RUN npm ci
COPY --from=sim index.html tsconfig.json vite.config.ts build-guard.ts ./
COPY --from=sim scenarios ./scenarios
COPY --from=sim src ./src
RUN VITE_VIEW_API_URL=/api npx vite build --base=/simulator/

# --- run: caddy 2 (alpine), pinned by digest, as a user with no rights
FROM caddy:2-alpine@sha256:78bd35025c7ba85961dd149ae670315129954e77abe2383ac47c89106b35ade9
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=dashboard /app/dist /srv/dashboard
COPY --from=simulator /app/dist /srv/simulator
USER 65534:65534
EXPOSE 8080

# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
#
# The API. Build from the repository root (the seed reads frontend/src/data/synthetic.json):
#
#     docker build -t biosentry-api .
#
# It is run by deploy/compose.yaml, which publishes it on the host's loopback only. Uvicorn binds
# 0.0.0.0 here because that is what a container needs to be reachable at all: the publish rule,
# never this file, is what keeps it off the public interface.
#
# To upgrade a pin, copy the new digest (`docker buildx imagetools inspect <tag>`).

# python:3.11-slim (Debian 13), pinned by digest.
ARG PYTHON=python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

# --- build: install exactly what uv.lock says (uv 0.12.17, pinned by digest), then leave uv behind
FROM ${PYTHON} AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/backend
# Dependencies first, so a source change does not reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
# The package is installed in place (editable): its code finds the dataset at /app/frontend/....
COPY backend/src ./src
COPY frontend/src/data/synthetic.json /app/frontend/src/data/synthetic.json
RUN uv sync --locked --no-dev

# --- run
FROM ${PYTHON}
ENV PYTHONUNBUFFERED=1 PATH="/app/backend/.venv/bin:$PATH"
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app/backend
COPY --from=build /app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "biosentry.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

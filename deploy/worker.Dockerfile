# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
#
# The two question workers (deploy/compose.yaml: `ask-patient` and `ask-lab`). Build from the
# repository root; deploy/worker.Dockerfile.dockerignore says what the build may read.
#
#     docker build -f deploy/worker.Dockerfile --target worker -t biosentry-worker .
#
# Two targets:
#   worker        the backend's packages and the research code. Rules only. This is the default.
#   worker-tuned  the same, plus the tuned model's own Python environment at /opt/tuned
#                 (MindSpore, CPU). Only deploy/compose.tuned.yaml builds it. The weights are never
#                 in the image: they are mounted read-only.
#
# The pins are the API image's (../Dockerfile): keep them in step.

ARG PYTHON=python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535
ARG UV=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc

FROM ${UV} AS uv

# --- build: the backend's packages, exactly as uv.lock says (the research code imports the backend)
FROM ${PYTHON} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --locked --no-dev

# --- worker: rules only
FROM ${PYTHON} AS worker
# PYTHONDONTWRITEBYTECODE: the root file system is read-only when compose runs this.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app PATH="/app/backend/.venv/bin:$PATH"
# /agent-home is where compose mounts the lab question worker's ledger volume: a new volume takes
# this folder's owner, so the worker (not root) can write it.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /agent-home && chown app /agent-home
WORKDIR /app
COPY --from=build /app /app
COPY research /app/research
USER app
CMD ["python", "-m", "research.patient_agent", "worker"]

# --- worker-tuned: plus the model's environment. The model runs as a child process under
# /opt/tuned/bin/python and imports nothing of the agent or the backend.
FROM worker AS worker-tuned
USER root
# MindSpore does not import without libgomp1.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
# The pinned list, without its notebook block (not needed to answer a question).
RUN sed '/^# --- notebook execution only ---/,$d' /app/research/agent_tuning/requirements.txt > /tmp/tuned.txt \
    && uv venv /opt/tuned \
    && uv pip install --python /opt/tuned/bin/python --only-binary :all: --no-cache -r /tmp/tuned.txt \
    && rm /tmp/tuned.txt /usr/local/bin/uv
USER app

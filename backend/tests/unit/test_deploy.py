"""The deployment files: what the server's stack publishes (the API and the web server on the host's
loopback, the database, the ward simulator and the lab worker nothing), pinned images, secrets that
have no defaults, the web server that hides the ingest door, and the two scripts, run here with a
stand-in for docker (the real stack was built, run, backed up and restored by hand; see
deploy/README.md)."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
SYSTEM_PATH = "/usr/bin:/bin"  # openssl, date, ls, chmod and friends; nothing else


def _lines(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]


def _code(path: Path) -> list[str]:
    return [ln for ln in _lines(path) if ln and not ln.startswith("#")]


# --- deploy/compose.yaml -----------------------------------------------------------------------


def _sim() -> list[str]:
    """The code lines of the `sim` service: up to the `lab` service that follows it."""
    code = _code(DEPLOY / "compose.yaml")
    return code[code.index("sim:") : code.index("lab:")]


def _lab() -> list[str]:
    """The code lines of the `lab` service: up to the `ask-patient` service that follows it."""
    code = _code(DEPLOY / "compose.yaml")
    return code[code.index("lab:") : code.index("ask-patient:")]


def _ask_patient() -> list[str]:
    """The code lines of the `ask-patient` service: up to the `ask-lab` service that follows it."""
    code = _code(DEPLOY / "compose.yaml")
    return code[code.index("ask-patient:") : code.index("ask-lab:")]


def _ask_lab() -> list[str]:
    """The code lines of the `ask-lab` service: up to the `web` service that follows it."""
    code = _code(DEPLOY / "compose.yaml")
    return code[code.index("ask-lab:") : code.index("web:")]


def _web() -> list[str]:
    """The code lines of the `web` service: the last one, before the top-level networks."""
    code = _code(DEPLOY / "compose.yaml")
    return code[code.index("web:") : code.index("networks:")]


def test_the_database_publishes_nothing_and_the_api_only_the_hosts_loopback() -> None:
    code = _code(DEPLOY / "compose.yaml")
    assert code.count("ports:") == 2  # only the API and the web server publish anything
    # No other spelling of a risky key anywhere (one line, quoted, or with a comment after it).
    risky = (
        r"""^['"]?(ports|expose|privileged|cap_add|devices|pid|ipc|userns_mode|network_mode|user"""
        r"""|entrypoint|<<)['"]?\s*:"""
    )
    assert [ln for ln in code if re.match(risky, ln)] == ["ports:", "ports:"]
    # The database's, the ward runner's, the lab question worker's, and the top-level block.
    assert [ln for ln in code if re.match(r"""^['"]?volumes['"]?\s*:""", ln)] == ["volumes:"] * 4
    published = [code[i + 1] for i, ln in enumerate(code) if ln == "ports:"]
    assert published == [  # both on the host's loopback; the variables move the host port only
        '- "127.0.0.1:${API_PORT:-8000}:8000"',
        '- "127.0.0.1:${WEB_PORT:-8080}:8080"',
    ]
    # Nothing else in the file is a port mapping (quoted, bare or in the long form), and none names
    # another address.
    mapping = r'^-\s*"?[^"\s]*:\d+(/\w+)?"?$|^published:'
    assert [ln for ln in code if re.search(mapping, ln)] == published
    assert "0.0.0.0" not in "\n".join(code)
    assert "network_mode: host" not in code  # no service runs on the host's network
    # Only the six builds (the API, the simulator, the lab worker, the two question workers, the web) do.
    assert code.count("network: host") == 6
    assert code.index("network: host") < code.index("restart: unless-stopped", code.index("build:"))
    postgres = code[code.index("postgres:") : code.index("api:")]
    assert "networks: [backend]" in postgres and "ports:" not in postgres
    # Only the top-level block is spelt `networks:` alone: a service names its networks as `[..]`,
    # so the _sim, _lab and _web slices end where they should and cannot hide one.
    assert code.count("networks:") == 1
    # The two networks the file makes are both internal: the database's, and the workers'.
    made = code[code.index("networks:") + 1 : code.index("volumes:", code.index("networks:"))]
    assert [ln.split(" #")[0] for ln in made] == ["backend:", "internal: true", "labnet:", "internal: true"]
    # Who is on which: the database alone with the API; the lab worker and the two question workers
    # each on the workers' network, with the API alone.
    assert [ln for ln in code if ln.startswith("networks: [")] == [
        "networks: [backend]",
        "networks: [default, backend, labnet]",
        "networks: [labnet]",
        "networks: [labnet]",
        "networks: [labnet]",
    ]


def test_the_stack_restarts_waits_for_a_healthy_database_and_keeps_the_data_in_a_volume() -> None:
    text = "\n".join(_code(DEPLOY / "compose.yaml"))
    assert text.count("restart: unless-stopped") == 7
    assert "condition: service_healthy" in text
    assert "pgdata:/var/lib/postgresql" in text and text.endswith("volumes:\npgdata:\nsimstate:\nagenthome:")
    assert "BIOSENTRY_ENV: production" in text


def test_secrets_have_no_defaults_so_a_missing_one_stops_compose() -> None:
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    for name in ("POSTGRES_PASSWORD", "BIOSENTRY_INGEST_KEY"):
        uses = re.findall(rf"\$\{{{name}[^}}]*\}}", text)
        assert uses and all(use.startswith(f"${{{name}:?") for use in uses), name
    assert "biosentry-dev-only" not in text


def test_the_ward_simulator_publishes_nothing_and_waits_for_a_healthy_api() -> None:
    sim = _sim()
    assert "ports:" not in sim and "expose:" not in sim and "network_mode: host" not in sim
    assert not any(ln.startswith("networks:") for ln in sim)  # the default network only
    assert "restart: unless-stopped" in sim
    wait = sim[sim.index("depends_on:") :]
    assert wait[:3] == ["depends_on:", "api:", "condition: service_healthy"]
    assert "context: ${SIM_DIR:-../../biosentry-ward-sim}" in sim
    assert "BIOSENTRY_API_URL: http://api:8000" in sim  # through the API, never the database
    assert "BIOSENTRY_INGEST_KEY: ${BIOSENTRY_INGEST_KEY:?set BIOSENTRY_INGEST_KEY in deploy/.env}" in sim
    assert "WARD_SPEED: ${WARD_SPEED:-1}" in sim
    assert "WARD_STATE_FILE: /state/ward-state.json" in sim
    assert sim[sim.index("volumes:") + 1] == "- simstate:/state"
    assert not any("POSTGRES" in ln or "DATABASE" in ln for ln in sim)


def test_the_lab_worker_publishes_nothing_reaches_only_the_api_and_can_write_only_in_memory() -> None:
    # The whole service, line for line. A key this list does not have (a port, a volume, a capability,
    # a user, a second network), in any YAML spelling or with a comment after it, fails here.
    assert _lab() == [
        "lab:",
        "build:",
        "context: ${SIM_DIR:-../../biosentry-ward-sim}",  # the image the runner is built from
        "network: host",
        'command: ["node", "dist/lab/lab-worker.js"]',
        "restart: unless-stopped",
        "depends_on:",
        "api:",
        "condition: service_healthy",
        "environment:",  # the API's address inside compose and the key, no default; no proxy variable
        "BIOSENTRY_API_URL: http://api:8000",
        "BIOSENTRY_INGEST_KEY: ${BIOSENTRY_INGEST_KEY:?set BIOSENTRY_INGEST_KEY in deploy/.env}",
        "networks: [labnet]",  # one internal network, shared with the API alone
        "read_only: true",
        'tmpfs: ["/tmp:size=64m"]',  # the one place it writes, in memory and capped
        "cap_drop: [ALL]",
        'security_opt: ["no-new-privileges:true"]',
        "mem_limit: 1g",  # a run that goes wrong cannot take the database's memory
        "pids_limit: 128",
        "cpus: 2",
    ]


def test_the_patient_question_worker_publishes_nothing_reaches_only_the_api_and_writes_nowhere() -> None:
    # The whole service, line for line, as for the lab worker. No volume at all: it writes no file.
    assert _ask_patient() == [
        "ask-patient:",
        "build:",
        "context: ..",
        "dockerfile: deploy/worker.Dockerfile",
        "target: worker",  # the rules-only image: no model environment
        "network: host",
        'command: ["python", "-m", "research.patient_agent", "worker"]',
        "restart: unless-stopped",
        "depends_on:",
        "api:",
        "condition: service_healthy",
        "environment:",  # the API's address inside compose and the key, no default; no proxy variable
        "BIOSENTRY_API_URL: http://api:8000",
        "BIOSENTRY_INGEST_KEY: ${BIOSENTRY_INGEST_KEY:?set BIOSENTRY_INGEST_KEY in deploy/.env}",
        "networks: [labnet]",  # one internal network, shared with the API alone
        "read_only: true",
        "cap_drop: [ALL]",
        'security_opt: ["no-new-privileges:true"]',
        "mem_limit: 256m",
        "pids_limit: 64",
        "cpus: 1",
    ]


def test_the_lab_question_worker_publishes_nothing_reaches_only_the_api_and_reads_only_the_trials() -> None:
    # The whole service, line for line. Its two folders: the trial root, read-only, and its own ledger.
    assert _ask_lab() == [
        "ask-lab:",
        "build:",
        "context: ..",
        "dockerfile: deploy/worker.Dockerfile",
        "target: worker",  # the rules-only image; deploy/compose.tuned.yaml is what adds the model
        "network: host",
        'command: ["python", "-m", "research.agent", "worker"]',
        "restart: unless-stopped",
        "depends_on:",
        "api:",
        "condition: service_healthy",
        "environment:",
        "BIOSENTRY_API_URL: http://api:8000",
        "BIOSENTRY_INGEST_KEY: ${BIOSENTRY_INGEST_KEY:?set BIOSENTRY_INGEST_KEY in deploy/.env}",
        "BIOSENTRY_AGENT_TRIALS: /trials",
        "BIOSENTRY_AGENT_HOME: /agent-home",
        "volumes:",
        "- ${AGENT_TRIALS_DIR:-./agent-trials}:/trials:ro",  # read-only
        "- agenthome:/agent-home",
        "networks: [labnet]",
        "read_only: true",
        'tmpfs: ["/tmp:size=64m"]',
        "cap_drop: [ALL]",
        'security_opt: ["no-new-privileges:true"]',
        "mem_limit: 1g",
        "pids_limit: 128",
        "cpus: 2",
    ]


def test_the_tuned_model_file_only_adds_read_only_weights_to_the_lab_question_worker() -> None:
    # The optional file changes one service. Its mounts are read-only, and it opens no port, no network
    # and no capability. The model's folders are named by variables that have no default.
    code = _code(DEPLOY / "compose.tuned.yaml")
    assert code == [
        "services:",
        "ask-lab:",
        "build:",
        "target: worker-tuned",
        "environment:",
        "BIOSENTRY_AGENT_TUNED_BASE: /weights/base",
        "BIOSENTRY_AGENT_TUNED_ADAPTER: /weights/adapter",
        "BIOSENTRY_AGENT_TUNED_PYTHON: /opt/tuned/bin/python",
        "volumes:",
        "- ${AGENT_WEIGHTS_DIR:?set AGENT_WEIGHTS_DIR in deploy/.env}/base:/weights/base:ro",
        "- ${AGENT_WEIGHTS_DIR:?set AGENT_WEIGHTS_DIR in deploy/.env}/adapter:/weights/adapter:ro",
        "mem_limit: 7g",
        "cpus: 4",
    ]


def test_the_worker_image_is_pinned_runs_unprivileged_and_leaves_tests_and_weights_out() -> None:
    text = (DEPLOY / "worker.Dockerfile").read_text(encoding="utf-8")
    from_lines = [ln for ln in text.splitlines() if ln.startswith("FROM ")]
    assert from_lines[:2] == ["FROM ${UV} AS uv", "FROM ${PYTHON} AS build"]
    for arg in ("PYTHON", "UV"):  # the base images are pinned by digest
        assert re.search(rf"^ARG {arg}=\S+@sha256:[0-9a-f]{{64}}$", text, re.M), arg
    assert "uv sync --locked --no-dev" in text  # the lock file, with its hashes
    assert "USER app" in text and "--uid 10001" in text
    ignore = (DEPLOY / "worker.Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignore[ignore.index("*") + 1 :][:3] == ["!backend/pyproject.toml", "!backend/uv.lock", "!backend/src"]
    for kept_out in ("research/agent/eval_questions.json", "research/agent/records",
                     "research/agent_tuning/heldout_frames.json", "research/patient_agent/eval_questions.json"):
        assert kept_out in ignore  # no evaluation question, no run record
    assert not any(ln.startswith("!") and ("tests" in ln or "models" in ln or "data" in ln) for ln in ignore)


def test_the_web_server_publishes_only_the_hosts_loopback_and_holds_no_secret() -> None:
    web = _web()
    assert web[web.index("ports:") + 1] == '- "127.0.0.1:${WEB_PORT:-8080}:8080"'
    assert web.count("ports:") == 1 and "expose:" not in web and "network_mode: host" not in web
    assert not any(ln.startswith("networks:") for ln in web)  # the default network only: no database
    assert not any(word in ln for ln in web for word in ("POSTGRES", "DATABASE", "INGEST", "env_file"))
    assert "read_only: true" in web and "cap_drop: [ALL]" in web
    assert 'security_opt: ["no-new-privileges:true"]' in web
    assert "restart: unless-stopped" in web
    wait = web[web.index("depends_on:") :]
    assert wait[:3] == ["depends_on:", "api:", "condition: service_healthy"]
    assert "sim: ${SIM_DIR:-../../biosentry-ward-sim}" in web  # the checkout the runner is built from


def test_the_web_image_is_pinned_runs_unprivileged_and_bakes_in_only_the_api_path() -> None:
    lines = _lines(DEPLOY / "web.Dockerfile")
    assert lines[0].startswith("# syntax=docker/dockerfile:") and "@sha256:" in lines[0]
    code = _code(DEPLOY / "web.Dockerfile")
    stages = [ln for ln in code if ln.startswith("FROM ")]
    assert stages[:2] == ["FROM ${NODE} AS dashboard", "FROM ${NODE} AS simulator"]
    assert len(stages) == 3 and stages[2].startswith("FROM caddy:")
    for line in (next(ln for ln in code if ln.startswith("ARG NODE=")), stages[2]):
        assert re.search(r"@sha256:[0-9a-f]{64}$", line), line
    assert [ln for ln in code if ln.startswith("USER ")] == ["USER 65534:65534"]
    # The one setting built into each page is the path /api on its own address: no host, no key.
    assert [ln for ln in code if "VITE_" in ln] == [
        "RUN VITE_API_URL=/api npx vite build",
        "RUN VITE_VIEW_API_URL=/api npx vite build --base=/simulator/",
    ]
    assert code.count("RUN npm ci") == 2  # from each lock file
    copied_from = [ln.split()[1] for ln in code if ln.startswith("COPY --from=")]
    assert set(copied_from) <= {  # the two checkouts and the two build stages: no image pulled unpinned
        "--from=frontend", "--from=sim", "--from=dashboard", "--from=simulator"
    }
    assert not any("INGEST" in ln or ".env" in ln for ln in code)
    assert _code(DEPLOY / ".dockerignore") == ["*", "!Caddyfile"]  # deploy/.env never reaches a build


def test_the_web_server_hides_the_ingest_door_and_passes_requests_only_to_the_api() -> None:
    code = _code(DEPLOY / "Caddyfile")
    assert "admin off" in code
    assert ":8080 {" in code and not any(ln.startswith(("tls", "bind")) for ln in code)
    api = code[code.index("handle_path /api/* {") :]
    # Refused before anything is passed on: `respond` runs ahead of `reverse_proxy` in one block.
    assert api[1:7] == [
        "@ingest path /sim /sim/*",
        "respond @ingest 404",
        "request_body {",  # a body over the limit is refused here (413) and never reaches the API
        "max_size 1MB",
        "}",
        "reverse_proxy api:8000",
    ]
    assert [ln for ln in code if "reverse_proxy" in ln] == ["reverse_proxy api:8000"]
    assert "browse" not in " ".join(code)  # no directory listing
    # For the day the address is public: HTTPS only from then on, and a slow client is dropped.
    assert "Strict-Transport-Security max-age=31536000" in code
    assert code[code.index("timeouts {") + 1 :][:3] == ["read_header 10s", "read_body 30s", "idle 2m"]
    assert not any(ln.startswith("log") for ln in code)  # no access log: nothing to leak a token into


# --- deploy/compose.public.yaml ----------------------------------------------------------------


def test_public_mode_publishes_nothing_and_pins_the_origins_and_the_open_view_off() -> None:
    # The whole file, line for line: public mode adds no port, no network, no volume and no service.
    capped = ["logging:", "driver: json-file", "options:", "max-size: 10m", 'max-file: "3"']
    assert _code(DEPLOY / "compose.public.yaml") == [
        "services:",
        "api:",
        "environment:",
        # The two public sites, exact origins: no wildcard, no third one.
        "BIOSENTRY_CORS_ORIGINS: https://biosentry-dashboard.pages.dev,https://biosentry-ward-sim.pages.dev",
        'BIOSENTRY_OPEN_WARD_VIEW: "false"',  # never watched without a sign-in while public
        *capped,  # a flood of requests cannot fill the disk with log lines
        "web:",
        *capped,
    ]


def test_the_database_image_is_the_one_the_dev_compose_uses() -> None:
    def image(path: Path) -> str:
        return next(ln for ln in _lines(path) if ln.startswith("image: postgres:"))

    pinned = image(DEPLOY / "compose.yaml")
    assert pinned == image(REPO / "compose.yaml") and re.search(r"@sha256:[0-9a-f]{64}$", pinned)


def test_the_deploy_env_file_is_git_ignored() -> None:
    assert "/deploy/.env" in _lines(REPO / ".gitignore")


# --- Dockerfile --------------------------------------------------------------------------------


def test_every_image_the_dockerfile_uses_is_pinned_by_digest() -> None:
    dockerfile = _lines(REPO / "Dockerfile")
    assert dockerfile[0].startswith("# syntax=docker/dockerfile:") and "@sha256:" in dockerfile[0]
    pulled = [ln for ln in dockerfile if re.match(r"(ARG PYTHON=|COPY --from=\S*[/:])", ln)]
    assert len(pulled) == 2  # the Python base (both stages use it) and uv
    for line in pulled:
        assert re.search(r"@sha256:[0-9a-f]{64}", line), line
    assert [ln for ln in dockerfile if ln.startswith("FROM ")] == [
        "FROM ${PYTHON} AS build",
        "FROM ${PYTHON}",
    ]


def test_the_image_runs_as_a_user_that_is_not_root_from_the_lock_file() -> None:
    code = _code(REPO / "Dockerfile")
    assert [ln for ln in code if ln.startswith("USER ")] == ["USER app"]
    assert "RUN uv sync --locked --no-dev" in code
    cmd = next(ln for ln in code if ln.startswith("CMD "))
    assert "uvicorn" in cmd and '"0.0.0.0"' in cmd  # in the container; the publish rule is the guard


def test_the_build_context_is_only_what_the_image_needs() -> None:
    rules = _code(REPO / ".dockerignore")
    assert rules[0] == "*"  # everything is out unless let back in
    assert "!frontend/src/data/synthetic.json" in rules and "!backend/uv.lock" in rules
    assert not any(".env" in rule or "node_modules" in rule for rule in rules)


# --- the scripts, with a docker that only writes down how it was called -------------------------


@pytest.fixture
def host(tmp_path: Path) -> dict[str, Path]:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("setup.sh", "backup.sh"):
        shutil.copy(DEPLOY / name, deploy / name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        '#!/bin/sh\necho "docker $*" >> "$LOG"\ncase "$*" in *pg_dump*) printf %s "$DUMP";; esac\n',
        encoding="utf-8",
    )
    (bin_dir / "tailscale").write_text('#!/bin/sh\necho "tailscale $*" >> "$LOG"\n', encoding="utf-8")
    (bin_dir / "git").write_text(  # a clone makes its directory, as the real one does
        '#!/bin/sh\necho "git $*" >> "$LOG"\ncase "$1" in clone) mkdir -p "$3";; esac\n', encoding="utf-8"
    )
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    return {"deploy": deploy, "bin": bin_dir, "log": tmp_path / "calls.log", "home": tmp_path}


def _run(
    host: dict[str, Path], script: str, path: str | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    full = {
        "PATH": path if path is not None else f"{host['bin']}:{SYSTEM_PATH}",
        "LOG": str(host["log"]),
        "HOME": str(host["home"]),
        "SIM_DIR": str(host["home"] / "biosentry-ward-sim"),
        "DUMP": "PGDMP-not-empty",
        **env,
    }
    return subprocess.run(
        ["/bin/sh", str(host["deploy"] / script)], env=full, capture_output=True, text=True, timeout=60
    )


def _calls(host: dict[str, Path]) -> list[str]:
    return host["log"].read_text(encoding="utf-8").splitlines() if host["log"].exists() else []


UP = "docker compose up -d --build --wait"


def test_setup_makes_a_private_env_file_without_printing_the_secrets_then_starts_the_stack(
    host: dict[str, Path],
) -> None:
    done = _run(host, "setup.sh")
    assert done.returncode == 0, done.stderr
    env_file = host["deploy"] / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    values = dict(ln.split("=", 1) for ln in env_file.read_text(encoding="utf-8").splitlines())
    assert re.fullmatch(r"[0-9a-f]{48}", values["POSTGRES_PASSWORD"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["BIOSENTRY_INGEST_KEY"])  # 64 >= the 32 required
    assert values["BIOSENTRY_CORS_ORIGINS"] == ""
    for secret in (values["POSTGRES_PASSWORD"], values["BIOSENTRY_INGEST_KEY"]):
        assert secret not in done.stdout + done.stderr
        assert secret not in "\n".join(_calls(host))
    assert _calls(host)[-1] == UP


PUBLIC_UP = "docker compose -f compose.yaml -f compose.public.yaml up -d --build --wait"


def _set(host: dict[str, Path], *lines: str) -> None:
    """Add lines to the deploy/.env that a first run of setup.sh made."""
    assert _run(host, "setup.sh").returncode == 0
    with (host["deploy"] / ".env").open("a", encoding="utf-8") as env_file:
        env_file.write("".join(f"{ln}\n" for ln in lines))
    host["log"].unlink()


def test_setup_stays_private_unless_a_public_host_is_set(host: dict[str, Path]) -> None:
    _set(host, "PUBLIC_HOST=")
    done = _run(host, "setup.sh")
    assert done.returncode == 0 and _calls(host)[-1] == UP
    assert "funnel" not in done.stdout and "tailscale serve" in done.stdout


def test_setup_adds_the_public_file_for_a_tailnet_name_and_opens_nothing_itself(
    host: dict[str, Path],
) -> None:
    _set(host, "PUBLIC_HOST=biosentry-api.tail8490cd.ts.net")
    done = _run(host, "setup.sh")
    assert done.returncode == 0, done.stderr
    assert _calls(host)[-1] == PUBLIC_UP and UP not in _calls(host)
    assert not any(call.startswith("tailscale") for call in _calls(host))  # it prints the command only
    assert "sudo tailscale funnel --bg --https=443 http://127.0.0.1:8080" in done.stdout
    assert "sudo tailscale funnel reset && sudo tailscale serve --bg --https=443" in done.stdout
    assert "VITE_API_URL=https://biosentry-api.tail8490cd.ts.net/api" in done.stdout
    assert "VITE_VIEW_API_URL=https://biosentry-api.tail8490cd.ts.net/api" in done.stdout


@pytest.mark.parametrize(
    "value",
    [
        "5-230-201-23.sslip.io",  # not the tailnet: this stack opens no port of its own
        "https://server.tail1234.ts.net",
        "server.tail1234.ts.net/api",
        "Server.Tail1234.ts.net",
        '"server.tail1234.ts.net"',
        "server.tail1234.ts.net; curl example.com",
        "evil.example/.ts.net",
        ".ts.net",
        "a..ts.net",
        "server.tail1234.ts.net.example.com",
        "server.evilts.net",  # the dot before ts.net is part of the rule
    ],
)
def test_setup_refuses_a_public_host_that_is_not_a_plain_tailnet_name(
    host: dict[str, Path], value: str
) -> None:
    _set(host, f"PUBLIC_HOST={value}")
    done = _run(host, "setup.sh")
    assert done.returncode == 1 and "PUBLIC_HOST" in done.stderr
    assert not any("up -d" in call for call in _calls(host))  # nothing was started
    assert "funnel" not in done.stdout


@pytest.mark.parametrize(
    "line",
    [  # every spelling compose reads as on
        "BIOSENTRY_OPEN_WARD_VIEW=true",
        "BIOSENTRY_OPEN_WARD_VIEW=TRUE",
        "BIOSENTRY_OPEN_WARD_VIEW= True ",
        'BIOSENTRY_OPEN_WARD_VIEW="true"',
        "BIOSENTRY_OPEN_WARD_VIEW='true'",
        "BIOSENTRY_OPEN_WARD_VIEW=true # on",
        "export BIOSENTRY_OPEN_WARD_VIEW=true",
        " BIOSENTRY_OPEN_WARD_VIEW=true",
        "BIOSENTRY_OPEN_WARD_VIEW = true",
        "BIOSENTRY_OPEN_WARD_VIEW: true",
    ],
)
def test_setup_refuses_public_mode_with_the_open_ward_view_on(host: dict[str, Path], line: str) -> None:
    _set(host, "PUBLIC_HOST=server.tail1234.ts.net", line)
    done = _run(host, "setup.sh")
    assert done.returncode == 1 and "BIOSENTRY_OPEN_WARD_VIEW" in done.stderr
    assert not any("up -d" in call for call in _calls(host))


@pytest.mark.parametrize(
    "line",
    [
        "export PUBLIC_HOST=server.tail1234.ts.net",
        " PUBLIC_HOST=server.tail1234.ts.net",
        "PUBLIC_HOST = server.tail1234.ts.net",
    ],
)
def test_setup_refuses_a_public_host_line_it_cannot_read_and_does_not_start_private(
    host: dict[str, Path], line: str
) -> None:
    _set(host, line)
    done = _run(host, "setup.sh")
    assert done.returncode == 1 and "PUBLIC_HOST=name" in done.stderr
    assert not any("up -d" in call for call in _calls(host))


def test_setup_allows_public_mode_with_the_open_ward_view_off(host: dict[str, Path]) -> None:
    _set(host, "BIOSENTRY_OPEN_WARD_VIEW=false", "PUBLIC_HOST=server.tail1234.ts.net")
    assert _run(host, "setup.sh").returncode == 0
    assert _calls(host)[-1] == PUBLIC_UP


def test_setup_is_safe_to_run_again_and_puts_the_mode_right(host: dict[str, Path]) -> None:
    _run(host, "setup.sh")
    env_file = host["deploy"] / ".env"
    first = env_file.read_text(encoding="utf-8")
    env_file.chmod(0o644)  # someone loosened it
    assert _run(host, "setup.sh").returncode == 0
    assert env_file.read_text(encoding="utf-8") == first  # the secrets are kept, not remade
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert _calls(host).count(UP) == 2


@pytest.mark.parametrize("missing", ["docker", "git", "tailscale", "openssl"])
def test_setup_stops_before_making_secrets_or_starting_anything_when_a_tool_is_missing(
    host: dict[str, Path], missing: str
) -> None:
    only = host["home"] / "only"  # a PATH of just what setup.sh needs, less the missing tool
    only.mkdir()
    needed = {tool: host["bin"] / tool for tool in ("docker", "git", "tailscale")}
    for tool in ("dirname", "chmod", "openssl"):
        needed[tool] = Path(shutil.which(tool) or "")
    for tool, source in needed.items():
        if tool != missing:
            (only / tool).symlink_to(source)
    done = _run(host, "setup.sh", path=str(only))
    assert done.returncode == 1 and f"{missing} is not installed" in done.stderr
    assert not (host["deploy"] / ".env").exists()
    assert UP not in _calls(host)


SIM_REPO = "https://github.com/kitchen-station/biosentry-ward-sim.git"


def test_setup_clones_the_ward_simulator_then_only_updates_it_and_does_so_before_building(
    host: dict[str, Path],
) -> None:
    sim = host["home"] / "biosentry-ward-sim"
    assert _run(host, "setup.sh").returncode == 0
    assert _calls(host) == ["docker compose version", f"git clone {SIM_REPO} {sim}", UP]
    assert sim.is_dir()
    assert _run(host, "setup.sh").returncode == 0  # there now: fast-forward only, never a second clone
    again = _calls(host)[3:]
    assert again == ["docker compose version", f"git -C {sim} pull --ff-only", UP]


def test_setup_stops_before_building_when_the_simulator_cannot_be_fetched(
    host: dict[str, Path],
) -> None:
    (host["bin"] / "git").write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    done = _run(host, "setup.sh")
    assert done.returncode == 7
    assert UP not in _calls(host)


def test_the_simulator_checkout_defaults_to_the_one_compose_builds_from() -> None:
    default = "${SIM_DIR:-../../biosentry-ward-sim}"
    assert default in (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    assert f"context: {default}" in _code(DEPLOY / "compose.yaml")


def test_a_missing_compose_plugin_is_refused(host: dict[str, Path]) -> None:
    (host["bin"] / "docker").write_text(
        '#!/bin/sh\n[ "$1 $2" = "compose version" ] && exit 1\nexit 0\n', encoding="utf-8"
    )
    done = _run(host, "setup.sh")
    assert done.returncode == 1 and "compose plugin" in done.stderr
    assert not (host["deploy"] / ".env").exists()


def _old_dumps(directory: Path, count: int) -> list[str]:
    names = []
    for day in range(1, count + 1):
        path = directory / f"biosentry-202601{day:02d}T030000Z.dump"
        path.write_text("old", encoding="utf-8")
        stamp = 1_700_000_000 + day * 86400  # all older than the one about to be made
        os.utime(path, (stamp, stamp))
        names.append(path.name)
    return names


def test_backup_writes_a_private_dated_dump_and_keeps_the_newest_14(host: dict[str, Path]) -> None:
    backups = host["home"] / "backups"
    backups.mkdir(mode=0o755)
    old = _old_dumps(backups, 20)
    done = _run(host, "backup.sh", BACKUP_DIR=str(backups))
    assert done.returncode == 0, done.stderr
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700  # made private, though it existed
    kept = sorted(p.name for p in backups.iterdir())
    assert len(kept) == 14
    new = Path(done.stdout.strip())
    assert new.name in kept and re.fullmatch(r"biosentry-\d{8}T\d{6}Z\.dump", new.name)
    assert new.read_text(encoding="utf-8") == "PGDMP-not-empty"
    assert stat.S_IMODE(new.stat().st_mode) == 0o600
    assert set(old[:7]).isdisjoint(kept) and set(old[7:]) <= set(kept)  # the oldest went
    dump = "docker compose exec -T postgres pg_dump -U biosentry -d biosentry --format=custom"
    assert dump in _calls(host)


def test_backup_defaults_to_a_private_directory_in_home_and_keep_can_be_changed(
    host: dict[str, Path],
) -> None:
    backups = host["home"] / "biosentry-backups"
    backups.mkdir()
    _old_dumps(backups, 5)
    assert _run(host, "backup.sh", KEEP="2").returncode == 0
    assert len(list(backups.iterdir())) == 2
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700


def test_an_empty_or_failed_dump_is_never_kept_as_a_backup(host: dict[str, Path]) -> None:
    backups = host["home"] / "backups"
    done = _run(host, "backup.sh", BACKUP_DIR=str(backups), DUMP="")
    assert done.returncode == 1 and "empty" in done.stderr
    assert list(backups.iterdir()) == []  # no .partial left behind either
    (host["bin"] / "docker").write_text("#!/bin/sh\necho partial\nexit 3\n", encoding="utf-8")
    assert _run(host, "backup.sh", BACKUP_DIR=str(backups)).returncode == 3
    assert list(backups.iterdir()) == []

"""The deployment files: what the VPS stack publishes (the API on the host's loopback, the database
nothing), pinned images, secrets that have no defaults, and the two scripts, run here with a stand-in
for docker (the real stack was built, run, backed up and restored by hand; see deploy/README.md)."""

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


def test_the_database_publishes_nothing_and_the_api_only_the_hosts_loopback() -> None:
    code = _code(DEPLOY / "compose.yaml")
    assert code.count("ports:") == 1  # only the API publishes anything
    assert code[code.index("ports:") + 1] == '- "127.0.0.1:8000:8000"'
    assert "network_mode: host" not in code  # no service runs on the host's network
    assert code[code.index("build:") :].count("network: host") == 1  # only the build does
    assert code.index("network: host") < code.index("restart: unless-stopped", code.index("build:"))
    postgres = code[code.index("postgres:") : code.index("api:")]
    assert "networks: [backend]" in postgres and "ports:" not in postgres
    assert any(ln.startswith("internal: true") for ln in code)  # the database's network


def test_the_stack_restarts_waits_for_a_healthy_database_and_keeps_the_data_in_a_volume() -> None:
    text = "\n".join(_code(DEPLOY / "compose.yaml"))
    assert text.count("restart: unless-stopped") == 2
    assert "condition: service_healthy" in text
    assert "pgdata:/var/lib/postgresql" in text and text.endswith("volumes:\npgdata:")
    assert "BIOSENTRY_ENV: production" in text


def test_secrets_have_no_defaults_so_a_missing_one_stops_compose() -> None:
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    for name in ("POSTGRES_PASSWORD", "BIOSENTRY_INGEST_KEY"):
        uses = re.findall(rf"\$\{{{name}[^}}]*\}}", text)
        assert uses and all(use.startswith(f"${{{name}:?") for use in uses), name
    assert "biosentry-dev-only" not in text


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
    (bin_dir / "tailscale").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
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


def test_setup_is_safe_to_run_again_and_puts_the_mode_right(host: dict[str, Path]) -> None:
    _run(host, "setup.sh")
    env_file = host["deploy"] / ".env"
    first = env_file.read_text(encoding="utf-8")
    env_file.chmod(0o644)  # someone loosened it
    assert _run(host, "setup.sh").returncode == 0
    assert env_file.read_text(encoding="utf-8") == first  # the secrets are kept, not remade
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert _calls(host).count(UP) == 2


@pytest.mark.parametrize("missing", ["docker", "tailscale", "openssl"])
def test_setup_stops_before_making_secrets_or_starting_anything_when_a_tool_is_missing(
    host: dict[str, Path], missing: str
) -> None:
    only = host["home"] / "only"  # a PATH of just what setup.sh needs, less the missing tool
    only.mkdir()
    needed = {"docker": host["bin"] / "docker", "tailscale": host["bin"] / "tailscale"}
    for tool in ("dirname", "chmod", "openssl"):
        needed[tool] = Path(shutil.which(tool) or "")
    for tool, source in needed.items():
        if tool != missing:
            (only / tool).symlink_to(source)
    done = _run(host, "setup.sh", path=str(only))
    assert done.returncode == 1 and f"{missing} is not installed" in done.stderr
    assert not (host["deploy"] / ".env").exists()
    assert UP not in _calls(host)


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

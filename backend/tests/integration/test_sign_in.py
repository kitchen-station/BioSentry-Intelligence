"""Sign-in: scrypt hashes, one identical refusal for every way to fail, a rate limit counted in the
audit log, every attempt audited without the passphrase, the dev passphrase only on loopback, and
the command that sets a passphrase."""

import io
import json
import logging
import runpy
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import dump_database, together
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from biosentry.api.app import SIGN_IN_FAILURES, create_app
from biosentry.core.config import Settings, is_loopback
from biosentry.db import passphrase as passphrase_module
from biosentry.db.passphrase import hash_passphrase, verify
from biosentry.db.seed import DEV_PASSPHRASE, ensure
from biosentry.repositories.postgres import connect

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
SENTINEL = "Sentinel-Passphrase-4471-xyz"


class Wall:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class Server:
    def __init__(self, database_url: str) -> None:
        self.url = database_url
        self.wall = Wall()
        self.client = TestClient(create_app(Settings(database_url=database_url), clock=self.wall))

    def sign_in(self, clinician_id: str, passphrase: str = DEV_PASSPHRASE) -> Any:
        body = {"clinician_id": clinician_id, "passphrase": passphrase}
        return self.client.post("/session", json=body)

    def audit(self) -> list[dict[str, Any]]:
        with connect(self.url) as conn:
            return conn.execute(
                "SELECT clinician_id, role, action, resource, outcome FROM audit_log"
                " WHERE action = 'sign_in' ORDER BY seq"
            ).fetchall()


@pytest.fixture
def server(database_url: str) -> Server:
    return Server(database_url)


@pytest.fixture
def hashes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry per scrypt run: every sign-in that gets as far as hashing adds exactly one."""
    calls: list[int] = []
    real = passphrase_module._scrypt

    def counting(*args: Any) -> bytes:
        calls.append(1)
        return real(*args)

    passphrase_module._dummy()  # built before counting starts
    monkeypatch.setattr(passphrase_module, "_scrypt", counting)
    return calls


# --- The hash ----------------------------------------------------------------------------------


def test_a_hash_carries_its_parameters_and_a_salt_of_its_own() -> None:
    first, second = hash_passphrase("correct horse battery"), hash_passphrase("correct horse battery")
    scheme, n, r, p, salt, digest = first.split("$")
    assert (scheme, int(n), int(r), int(p)) == ("scrypt", 2**15, 8, 1)
    assert first != second  # a random salt per hash
    assert verify("correct horse battery", first) and verify("correct horse battery", second)
    assert not verify("correct horse batterz", first)
    assert "correct horse" not in first and salt and digest


def test_verification_uses_the_parameters_stored_with_the_hash() -> None:
    import base64

    salt = b"0123456789abcdef"
    raw = passphrase_module._scrypt("twelve chars!", salt, 2**14, 8, 1, 32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    stored = f"scrypt$16384$8$1${b64(salt)}${b64(raw)}"  # a cost this code no longer writes
    assert verify("twelve chars!", stored) and not verify("twelve chars?", stored)


def test_the_comparison_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[bytes, bytes]] = []
    real = passphrase_module.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(passphrase_module.hmac, "compare_digest", spy)
    assert verify("twelve chars!", hash_passphrase("twelve chars!"))
    assert len(seen) == 1


@pytest.mark.parametrize("stored", [None, "", "not a hash", "bcrypt$1$2$3$4$5", "scrypt$1$2"])
def test_no_hash_or_one_that_is_not_ours_never_verifies_but_still_costs_a_hash(
    stored: str | None, hashes: list[int]
) -> None:
    assert verify(DEV_PASSPHRASE, stored) is False
    assert len(hashes) == 1  # hashed against the dummy: the same work as a real check


def test_no_more_than_two_scrypts_run_at_once_however_many_sign_ins_are_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running, peak = [0], [0]
    lock = threading.Lock()
    real = passphrase_module.hashlib.scrypt

    def slow(*args: Any, **kwargs: Any) -> bytes:
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)  # long enough for every other thread to arrive
        try:
            return real(*args, **kwargs)
        finally:
            with lock:
                running[0] -= 1

    monkeypatch.setattr(passphrase_module.hashlib, "scrypt", slow)
    stored = hash_passphrase("twelve chars!")
    peak[0] = 0
    results = together(10, lambda i: verify("twelve chars!", stored))
    assert results == [True] * 10  # all of them are served, only never more than two at once
    assert peak[0] == 2  # and two did overlap: the limit is not vacuous


def test_a_sign_in_that_gets_no_scrypt_slot_in_time_answers_503_and_leaves_no_row(
    server: "Server", monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(passphrase_module, "SLOT_WAIT", 0.01)
    for _ in range(2):
        passphrase_module._SLOTS.acquire()
    try:
        busy = server.sign_in("rn-manalo")
    finally:
        for _ in range(2):
            passphrase_module._SLOTS.release()
    assert busy.status_code == 503
    assert busy.headers["retry-after"] == "5"
    assert server.audit() == []
    assert server.sign_in("rn-manalo").status_code == 200  # the slots are free again


def test_a_locked_id_and_an_anonymous_caller_each_leave_one_audit_row_a_minute(
    server: "Server",
) -> None:
    def rows() -> tuple[int, int]:
        with connect(server.url) as conn:
            found = conn.execute("SELECT action, outcome FROM audit_log").fetchall()
        pairs = [(r["action"], r["outcome"]) for r in found]
        return pairs.count(("sign_in", "limited")), pairs.count(("authenticate", "denied"))

    for _ in range(SIGN_IN_FAILURES):
        server.sign_in("rn-manalo", "wrong passphrase")
    for _ in range(3):
        assert server.sign_in("rn-manalo").status_code == 429
        assert server.client.get("/patients").status_code == 401
    assert rows() == (1, 1)
    server.wall.now += timedelta(minutes=1, seconds=1)
    assert server.sign_in("rn-manalo").status_code == 429
    assert server.client.get("/patients").status_code == 401
    assert rows() == (2, 2)


# --- POST /session -----------------------------------------------------------------------------


def test_the_right_passphrase_gets_a_token_that_works(server: Server) -> None:
    response = server.sign_in("rn-manalo")
    assert response.status_code == 200 and set(response.json()) == {"token", "role"}
    headers = {"Authorization": f"Bearer {response.json()['token']}"}
    assert server.client.get("/patients", headers=headers).status_code == 200


def test_every_way_to_fail_is_the_same_401_after_the_same_work(
    server: Server, hashes: list[int]
) -> None:
    with connect(server.url) as conn:
        conn.execute("UPDATE clinician SET passphrase_hash = NULL WHERE id = 'rn-reyes'")
    refused = [
        server.sign_in("rn-manalo", "the wrong passphrase"),  # wrong
        server.sign_in("rn-nobody", DEV_PASSPHRASE),  # no such clinician
        server.sign_in("rn-reyes", DEV_PASSPHRASE),  # a clinician with no passphrase set
    ]
    assert [r.status_code for r in refused] == [401, 401, 401]
    assert len({(r.status_code, r.text, tuple(sorted(r.headers.items()))) for r in refused}) == 1
    assert refused[0].json() == {"detail": "Sign-in failed"}
    assert len(hashes) == 3  # each one hashed once, against a dummy where there was no real hash
    assert server.sign_in("rn-manalo").status_code == 200
    assert len(hashes) == 4


def test_a_known_id_with_no_hash_set_stays_out_whatever_is_sent(server: Server) -> None:
    with connect(server.url) as conn:
        conn.execute("UPDATE clinician SET passphrase_hash = NULL")
    for passphrase in (DEV_PASSPHRASE, "x", "scrypt$1$1$1$AA==$AA==", "a" * 256):
        assert server.sign_in("md-santos", passphrase).status_code == 401


def test_every_attempt_is_audited_with_ids_and_outcome_only(server: Server) -> None:
    long_id = "x" * 64
    server.sign_in("rn-manalo")
    server.sign_in("rn-manalo", SENTINEL)
    server.sign_in("rn-nobody", SENTINEL)
    server.sign_in(long_id, SENTINEL)
    rows = server.audit()
    assert [(r["clinician_id"], r["role"], r["outcome"]) for r in rows] == [
        ("rn-manalo", "charge_rn", "ok"),
        ("rn-manalo", "charge_rn", "denied"),
        (None, None, "denied"),
        (None, None, "denied"),
    ]
    assert [r["resource"] for r in rows[:2]] == ["rn-manalo", "rn-manalo"]
    for row in rows[2:]:  # an unknown id: a fixed-length digest, never what was sent
        assert row["resource"].startswith("unknown:") and len(row["resource"]) == 24
    assert rows[2]["resource"] != rows[3]["resource"]
    assert "rn-nobody" not in json.dumps(rows) and long_id not in json.dumps(rows)


def test_the_passphrase_is_never_stored_logged_or_audited(
    server: Server, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    server.sign_in("rn-manalo", SENTINEL)
    server.sign_in("rn-nobody", SENTINEL)
    token = server.sign_in("rn-manalo").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    mine = server.client.get("/audit", headers=headers).text
    with connect(server.url) as conn:
        dump = dump_database(conn)
    captured = capsys.readouterr()
    for place in (dump, mine, caplog.text, captured.out, captured.err):
        assert SENTINEL not in place and DEV_PASSPHRASE not in place


@pytest.mark.parametrize(
    "body",
    [
        {"clinician_id": "rn-manalo", "passphrase": SENTINEL + "z" * 300},  # over 256
        {"clinician_id": "rn-manalo", "passphrase": ""},
        {"clinician_id": "rn-manalo", "passphrase": 12345678901234},
        {"clinician_id": "rn-manalo", "passphrase": SENTINEL, "extra": SENTINEL},
        {"clinician_id": "rn-manalo", "passphrase": SENTINEL + "\x00"},
        {"clinician_id": "x" * 65, "passphrase": SENTINEL},
        {"clinician_id": "rn-manalo"},
        {"passphrase": SENTINEL},
        {},
    ],
    ids=["too-long", "empty", "number", "extra-field", "nul", "long-id", "no-passphrase", "no-id", "nothing"],
)
def test_a_refused_body_is_a_422_that_never_echoes_the_passphrase(
    server: Server, body: dict[str, Any]
) -> None:
    response = server.client.post("/session", json=body)
    assert response.status_code == 422
    assert SENTINEL not in response.text and "12345678901234" not in response.text
    for error in response.json()["detail"]:
        assert "input" not in error and "ctx" not in error
    assert server.audit() == []  # a malformed body is not an attempt


def test_the_longest_allowed_passphrase_is_checked_not_refused(server: Server) -> None:
    assert server.sign_in("rn-manalo", "a" * 256).status_code == 401
    assert server.sign_in("rn-manalo", "a" * 257).status_code == 422


# --- The rate limit ----------------------------------------------------------------------------


def test_five_failures_in_fifteen_minutes_lock_an_id_out_before_any_hashing(
    server: Server, hashes: list[int]
) -> None:
    for _ in range(SIGN_IN_FAILURES):
        assert server.sign_in("rn-manalo", "wrong passphrase").status_code == 401
    assert len(hashes) == 5
    server.wall.now += timedelta(minutes=10)
    locked = server.sign_in("rn-manalo")  # even the right passphrase is refused
    assert locked.status_code == 429
    assert locked.json() == {"detail": "Too many failed sign-in attempts. Try again in 15 minutes."}
    assert locked.headers["retry-after"] == "900"
    assert len(hashes) == 5  # refused before hashing
    assert server.sign_in("rn-reyes").status_code == 200  # another id is untouched
    assert [r["outcome"] for r in server.audit()] == ["denied"] * 5 + ["limited", "ok"]
    # The refusals above do not extend the lock: it ends 15 minutes after the failures.
    server.wall.now = NOW + timedelta(minutes=15, seconds=1)
    assert server.sign_in("rn-manalo").status_code == 200


def test_the_window_is_fifteen_minutes(server: Server) -> None:
    for _ in range(SIGN_IN_FAILURES - 1):
        server.sign_in("rn-manalo", "wrong passphrase")
    server.wall.now += timedelta(minutes=15, seconds=1)  # those four are out of the window
    assert server.sign_in("rn-manalo", "wrong passphrase").status_code == 401
    assert server.sign_in("rn-manalo").status_code == 200  # one recent failure: not locked


def test_a_success_is_not_a_failure(server: Server) -> None:
    for _ in range(SIGN_IN_FAILURES * 2):
        assert server.sign_in("rn-manalo").status_code == 200


def test_an_unknown_id_is_limited_like_a_known_one_so_429_proves_nothing(server: Server) -> None:
    known, unknown = [], []
    for _ in range(SIGN_IN_FAILURES + 1):
        known.append(server.sign_in("rn-manalo", "wrong passphrase"))
        unknown.append(server.sign_in("rn-nobody", "wrong passphrase"))
    assert [r.status_code for r in known] == [r.status_code for r in unknown]
    assert [r.status_code for r in known] == [401] * 5 + [429]
    assert known[-1].text == unknown[-1].text


def test_a_burst_of_simultaneous_attempts_cannot_all_pass_the_count(server: Server) -> None:
    sent = together(12, lambda i: server.sign_in("rn-manalo", f"wrong passphrase {i}"))
    codes = sorted(r.status_code for r in sent)
    assert codes == [401] * SIGN_IN_FAILURES + [429] * (12 - SIGN_IN_FAILURES)


# --- Dev passphrase: loopback only -------------------------------------------------------------


def _hashes(url: str) -> dict[str, str | None]:
    with connect(url) as conn:
        rows = conn.execute("SELECT id, passphrase_hash FROM clinician").fetchall()
    return {r["id"]: r["passphrase_hash"] for r in rows}


def test_seeding_over_a_loopback_connection_gives_every_clinician_the_dev_passphrase(
    blank_database_url: str,
) -> None:
    with connect(blank_database_url) as conn:
        assert is_loopback(conn.info)  # judged on the established connection
    ensure(blank_database_url)
    stored = _hashes(blank_database_url)
    assert len(stored) == 5 and all(verify(DEV_PASSPHRASE, h) for h in stored.values())
    assert len(set(stored.values())) == 5  # each with a salt of its own


def test_seeding_over_a_connection_that_is_not_loopback_gives_nobody_a_passphrase(
    blank_database_url: str, spoofed_server_address: None
) -> None:
    # The URL says 127.0.0.1, as with PGHOSTADDR or a service file pointing elsewhere.
    ensure(blank_database_url)
    assert set(_hashes(blank_database_url).values()) == {None}
    api = Server(blank_database_url)
    assert api.sign_in("rn-manalo").status_code == 401  # fails closed


@pytest.mark.parametrize(
    ("host", "hostaddr", "expected"),
    [
        ("127.0.0.1", "127.0.0.1", True),
        ("localhost", "127.0.0.1", True),
        ("localhost", "::1", True),
        ("::1", "::1", True),
        ("127.0.0.1", "8.8.8.8", False),  # PGHOSTADDR, a service file: the address is what counts
        ("localhost", "10.0.0.5", False),
        ("db.example.invalid", "10.0.0.5", False),
        ("db.example.invalid", "127.0.0.1", False),  # a remote name: not trusted as loopback
        ("127.0.0.1", "", False),  # no address to read
        ("/var/run/postgresql", "", False),  # a unix socket
        ("127.0.0.1", "not an address", False),
        (None, None, False),
    ],
)
def test_loopback_is_decided_from_what_the_connection_reports(
    host: str | None, hostaddr: str | None, expected: bool
) -> None:
    assert is_loopback(SimpleNamespace(host=host, hostaddr=hostaddr)) is expected


def test_a_connection_made_through_a_remote_looking_url_is_not_loopback(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real connection: the name says remote, PGHOSTADDR sends it to this machine anyway.
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    url = make_conninfo(database_url, host="db.example.invalid")
    with connect(url) as conn:
        assert (conn.info.host, conn.info.hostaddr) == ("db.example.invalid", "127.0.0.1")
        assert not is_loopback(conn.info)


# --- python -m biosentry.db.passphrase ---------------------------------------------------------


@pytest.fixture
def cli(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Server:
    monkeypatch.setenv("BIOSENTRY_DATABASE_URL", database_url)
    return Server(database_url)


def _run(stdin: str, *argv: str, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))  # not a terminal
    return passphrase_module.main(list(argv))


def test_resetting_a_passphrase_signs_that_clinician_out_and_no_one_else(
    cli: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = {"Authorization": f"Bearer {cli.sign_in('rn-reyes').json()['token']}"}
    other = {"Authorization": f"Bearer {cli.sign_in('md-santos').json()['token']}"}
    assert cli.client.get("/patients", headers=old).status_code == 200
    assert _run("a brand new passphrase\n", "rn-reyes", monkeypatch=monkeypatch) == 0
    assert cli.client.get("/patients", headers=old).status_code == 401  # the old token is dead
    assert cli.client.get("/patients", headers=other).status_code == 200  # others are untouched
    new = cli.sign_in("rn-reyes", "a brand new passphrase")
    assert new.status_code == 200  # and the new passphrase signs in afresh


def test_the_command_reads_stdin_and_the_new_passphrase_signs_in(
    cli: Server, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run("a brand new passphrase\n", "rn-reyes", monkeypatch=monkeypatch) == 0
    assert capsys.readouterr().out == "passphrase set\n"
    assert cli.sign_in("rn-reyes", "a brand new passphrase").status_code == 200
    assert cli.sign_in("rn-reyes").status_code == 401  # the dev passphrase is replaced
    assert "a brand new passphrase" not in dump_database_text(cli.url)


def dump_database_text(url: str) -> str:
    with connect(url) as conn:
        return dump_database(conn)


@pytest.mark.parametrize("entered", ["", "short", "a" * 11, "a" * 257, "twelve chars\x00!"])
def test_the_command_refuses_a_passphrase_outside_12_to_256_characters(
    cli: Server, monkeypatch: pytest.MonkeyPatch, entered: str
) -> None:
    before = _hashes(cli.url)
    with pytest.raises(SystemExit, match="12 to 256"):
        _run(entered + "\n", "rn-reyes", monkeypatch=monkeypatch)
    assert _hashes(cli.url) == before


def test_the_command_refuses_an_unknown_clinician(
    cli: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit, match="no such clinician"):
        _run("a perfectly good passphrase\n", "rn-nobody", monkeypatch=monkeypatch)


def test_on_a_terminal_it_prompts_twice_with_getpass(
    cli: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["terminal passphrase", "terminal passphrase", "one", "two"])
    monkeypatch.setattr(passphrase_module.getpass, "getpass", lambda prompt: next(answers))
    monkeypatch.setattr(sys, "stdin", type("Tty", (io.StringIO,), {"isatty": lambda self: True})())
    assert passphrase_module.main(["md-aquino"]) == 0
    assert cli.sign_in("md-aquino", "terminal passphrase").status_code == 200
    with pytest.raises(SystemExit, match="differ"):  # a typo must not lock someone out
        passphrase_module.main(["md-aquino"])


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_the_command_runs_as_a_module(cli: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["biosentry.db.passphrase", "md-santos"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("run as a module ok\n"))
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("biosentry.db.passphrase", run_name="__main__")
    assert exited.value.code == 0
    assert cli.sign_in("md-santos", "run as a module ok").status_code == 200

"""The `skip` command: a clinician who may control the sim asks the whole run to move ahead 1 h, 6 h,
12 h or 1 day. One kind of command, one field (`minutes`), the roles of `set_speed`, and one skip
at a time: a new one is refused (409) while an earlier skip has not been picked up by the sim."""

from datetime import timedelta
from typing import Any

import pytest
from test_overview import ADMIN, add_admin
from test_store import Api, api, command, commands, sent, sim_audit, start  # noqa: F401 (api: fixture)

ALLOWED = (60, 360, 720, 1440)
SKIP = {"kind": "skip", "minutes": 60}


def test_each_allowed_value_goes_on_the_feed_with_its_minutes_for_the_whole_run(api: Api) -> None:
    run_id = start(api)
    who_sent = ("md-santos", "rn-manalo", "md-aquino", "md-santos")
    for who, minutes in zip(who_sent, ALLOWED, strict=True):
        assert command(api, who, kind="skip", minutes=minutes).status_code == 204
        row = sent(api, run_id)[-1]
        assert (row["kind"], row["minutes"], row["speed"], row["patient_id"], row["clinician_id"]) == (
            "skip", minutes, None, None, who
        )
        sent(api, run_id, after=row["seq"])  # the sim picks it up: the next skip may be sent
    assert sim_audit(api) == [(who, "W4", "ok") for who in who_sent]


def test_one_skip_waits_for_the_sim_and_a_second_is_409_and_audited(api: Api) -> None:
    run_id = start(api)
    assert command(api, "md-santos", **SKIP).status_code == 204
    [first] = sent(api, run_id)
    for who in ("md-santos", "rn-manalo"):
        refused = command(api, who, kind="skip", minutes=1440)
        assert refused.status_code == 409 and "skip" in refused.json()["detail"]
    assert command(api, "md-santos", kind="set_speed", speed=10).status_code == 204  # other commands pass
    assert command(api, "md-santos", kind="reset").status_code == 204
    assert [r["kind"] for r in sent(api, run_id)] == ["skip", "set_speed", "reset"]  # nothing queued behind it
    sent(api, run_id, after=first["seq"] - 1)  # a read that stops before the skip does not clear it
    assert command(api, "md-santos", **SKIP).status_code == 409
    sent(api, run_id, after=first["seq"])  # picked up
    assert command(api, "md-santos", kind="skip", minutes=360).status_code == 204
    sent(api, run_id, after=0)  # a cursor never moves back: the new skip is still waiting
    assert command(api, "md-santos", **SKIP).status_code == 409
    assert [o for _, _, o in sim_audit(api)] == [
        "ok", "rejected", "rejected", "ok", "ok", "rejected", "ok", "rejected",
    ]  # fmt: skip


def test_a_waiting_skip_of_an_older_run_does_not_hold_the_new_one(api: Api) -> None:
    old = start(api, client_id="client-a")
    assert command(api, "md-santos", **SKIP).status_code == 204
    api.wall.now += timedelta(seconds=31)
    new = start(api, client_id="client-b")
    assert new != old and command(api, "md-santos", **SKIP).status_code == 204
    assert [r["kind"] for r in sent(api, new)] == ["skip"]


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "skip"},
        {"kind": "skip", "minutes": None},
        *({"kind": "skip", "minutes": m} for m in (0, 1, 59, 61, 120, 1441, 10080, -60, "60", 60.0, True, [60])),
        {"kind": "skip", "minutes": 60, "speed": 10},
        {"kind": "skip", "minutes": 60, "patient_id": "s-1"},
        {"kind": "set_speed", "speed": 10, "minutes": 60},
        {"kind": "reset", "minutes": 60},
        {"kind": "apply_patch", "patient_id": "s-1", "minutes": 60},
    ],
)
def test_minutes_is_one_of_four_and_only_for_skip_else_422_and_no_row(api: Api, body: dict[str, Any]) -> None:
    run_id = start(api)
    assert command(api, "md-santos", **body).status_code == 422
    assert sent(api, run_id) == []
    assert sim_audit(api) == [("md-santos", "-", "rejected")]


def test_the_roles_are_those_of_set_speed_never_an_admin_never_anonymous(api: Api) -> None:
    run_id = start(api)
    add_admin(api)
    for who in ("rn-delacruz", "rn-reyes", ADMIN):
        assert command(api, who, **SKIP).status_code == 403, who
        assert command(api, who, **SPEED_BODY).status_code == 403, who  # the same refusal as set_speed
    assert api.client.post("/wards/W4/sim/commands", json=SKIP).status_code == 401
    assert sent(api, run_id) == []
    assert {o for _, _, o in sim_audit(api)} == {"denied"}


SPEED_BODY = {"kind": "set_speed", "speed": 10}


def test_a_skip_when_the_ward_is_not_receiving_data_is_409_and_the_feed_needs_the_key(api: Api) -> None:
    run_id = start(api)
    api.wall.now += timedelta(seconds=31)  # the run's last batch is old: nothing would read the command
    assert command(api, "md-santos", **SKIP).status_code == 409
    assert sent(api, run_id) == []
    assert commands(api, run_id, key="wrong").status_code == 403

"""A timer-driven read repeated by the same clinician is recorded at most once a minute; the first
read, every other outcome and every other action are always recorded."""

from datetime import timedelta
from typing import Any

import pytest
from test_overview import ADMIN, add_admin, audit_of
from test_store import CLINICIANS, Api, api  # noqa: F401 (api: fixture)

from biosentry.core.audit import REPEATED_VIEWS, VIEW_ROW_EVERY, AuditLog
from biosentry.repositories.postgres import connect

RN, MD = "rn-manalo", "md-aquino"
PID = "p-003"  # md-aquino's patient
READS = [
    ("view_ward", "/wards/W4/rows", "W4"),
    ("view_live", "/wards/W4/live", "W4"),
    ("view_patient", f"/patients/{PID}", PID),
    ("view_baseline", f"/patients/{PID}/baseline", PID),
    ("view_lab_runs", "/wards/W4/lab/runs", "W4"),
    ("view_lab_questions", "/wards/W4/lab/questions", "W4"),
]


def get(api: Api, path: str, who: str = MD) -> Any:
    return api.client.get(path, headers=api.as_(who))


def rows(api: Api, who: str, action: str | None = None) -> list[dict[str, Any]]:
    return api.sql(
        "SELECT clinician_id, role, action, resource, outcome, at FROM audit_log"
        " WHERE clinician_id = %s AND (%s::text IS NULL OR action = %s) AND action <> 'sign_in'"
        " ORDER BY seq",
        who,
        action,
        action,
    )


def test_the_rule_names_six_reads_and_one_minute() -> None:
    assert VIEW_ROW_EVERY == timedelta(minutes=1)
    assert REPEATED_VIEWS == {a for a, _, _ in READS}


@pytest.mark.parametrize(("action", "path", "resource"), READS)
def test_two_reads_in_a_minute_leave_one_row_and_a_later_one_a_second(
    api: Api, action: str, path: str, resource: str
) -> None:
    who = RN if action == "view_live" or action == "view_lab_runs" else MD
    first = get(api, path, who)
    api.wall.now += timedelta(seconds=59)
    second = get(api, path, who)
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()  # the data is served on every call
    assert len(rows(api, who, action)) == 1
    api.wall.now += timedelta(seconds=2)  # 61 seconds after the first row
    assert get(api, path, who).status_code == 200
    written = rows(api, who, action)
    assert [(r["clinician_id"], r["action"], r["resource"], r["outcome"]) for r in written] == [
        (who, action, resource, "ok")
    ] * 2
    assert written[0]["role"] == written[1]["role"] == ("charge_rn" if who == RN else "md")
    assert written[1]["at"] - written[0]["at"] == timedelta(seconds=61)


def test_two_clinicians_reading_the_same_list_leave_a_row_each(api: Api) -> None:
    for who in (RN, MD, RN, MD):
        get(api, "/wards/W4/rows", who)
    assert [r["clinician_id"] for r in api.sql("SELECT clinician_id FROM audit_log WHERE action = 'view_ward' ORDER BY seq")] == [RN, MD]


def test_one_clinician_reading_two_patients_leaves_a_row_each(api: Api) -> None:
    mine = CLINICIANS[MD][:2]
    assert len(mine) == 2
    for pid in (*mine, *mine):
        get(api, f"/patients/{pid}", MD)
    assert [r["resource"] for r in rows(api, MD, "view_patient")] == mine


def test_view_ward_and_view_live_in_one_minute_are_separate_streams(api: Api) -> None:
    for _ in range(2):
        get(api, "/wards/W4/rows", RN)
        get(api, "/wards/W4/live", RN)
    assert [r["action"] for r in rows(api, RN)] == ["view_ward", "view_live"]


def test_an_admin_ward_read_twice_in_a_minute_leaves_one_row(api: Api) -> None:
    headers = add_admin(api)
    for _ in range(2):
        assert api.client.get("/wards/W4/rows", headers=headers).status_code == 200
    assert audit_of(api, ADMIN) == [("view_ward", "W4", "ok")]
    assert rows(api, ADMIN)[0]["role"] == "admin"


def test_a_missing_patient_read_twice_in_a_minute_leaves_two_rows(api: Api) -> None:
    for _ in range(2):
        assert get(api, "/patients/p-001", MD).status_code == 404
    assert [(r["resource"], r["outcome"]) for r in rows(api, MD, "view_patient")] == [
        ("p-001", "not_found")
    ] * 2


def test_an_admin_refused_on_live_twice_in_a_minute_leaves_two_denied_rows(api: Api) -> None:
    headers = add_admin(api)
    for _ in range(2):
        assert api.client.get("/wards/W4/live", headers=headers).status_code == 403
    assert audit_of(api, ADMIN) == [("view_live", "W4", "denied")] * 2


def test_writes_notes_and_the_overview_are_never_limited(api: Api) -> None:
    note = {"author": "a", "role": "MD", "content": "c"}
    for _ in range(2):
        assert api.client.post(f"/patients/{PID}/notes", headers=api.as_(MD), json=note).status_code == 201
        assert api.client.post(f"/patients/{PID}/labs", headers=api.as_(MD), json={"potassium": 4.0}).status_code == 200
        assert get(api, f"/patients/{PID}/notes", MD).status_code == 200
    names = [r["action"] for r in rows(api, MD)]
    assert names.count("add_note") == names.count("enter_labs") == names.count("view_notes") == 2
    headers = add_admin(api)
    for _ in range(2):
        assert api.client.get("/wards/W4/overview", headers=headers).status_code == 200
    assert audit_of(api, ADMIN) == [("view_overview", "W4", "ok")] * 2


def test_a_sign_in_is_never_limited(api: Api) -> None:
    from biosentry.db.seed import DEV_PASSPHRASE

    for _ in range(2):
        body = {"clinician_id": MD, "passphrase": DEV_PASSPHRASE}
        assert api.client.post("/session", json=body).status_code == 200
    assert len(api.sql("SELECT 1 FROM audit_log WHERE action = 'sign_in' AND clinician_id = %s", MD)) == 2


def test_a_not_found_row_in_the_last_minute_does_not_hide_the_next_ok_read(api: Api) -> None:
    with connect(api.url) as conn:
        log = AuditLog(conn, api.wall, lambda _: "md")
        log.append(MD, "view_patient", PID, "not_found")
        log.append_view(MD, "view_patient", PID)
        log.append_view(MD, "view_patient", PID)
    assert [r["outcome"] for r in rows(api, MD, "view_patient")] == ["not_found", "ok"]


def test_a_row_stamped_after_now_does_not_hide_a_read(api: Api) -> None:
    with connect(api.url) as conn:
        log = AuditLog(conn, api.wall, lambda _: "charge_rn")
        log.append_view(RN, "view_ward", "W4")
        api.wall.now -= timedelta(minutes=5)  # the host clock steps back
        log.append_view(RN, "view_ward", "W4")
        log.append_view(RN, "view_ward", "W4")  # the same minute again: still one row for it
    assert len(rows(api, RN, "view_ward")) == 2

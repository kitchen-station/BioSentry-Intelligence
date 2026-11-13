"""The Simulation lab (contract-d7): a clinician's request, the lab worker's claim and report, the
limits, the timeouts and the table's own checks. The API only stores; nothing here runs anything."""

import json
import threading
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from conftest import dump_database
from test_overview import ADMIN, add_admin, audit_of, write
from test_store import KEY, Api, api, iso  # noqa: F401 (api: fixture)

from biosentry.repositories.postgres import connect
from biosentry.schemas.lab import HOSPITAL_PAIRS, W4_RULES
from biosentry.services.lab import CLAIM

W4 = {
    "kind": "compare",
    "mode": "w4",
    "seed": 4,
    "days": 3,
    "baseline": "news2_total_ge_5",
    "change": "news2_chart_trigger",
}
HOSPITAL = {**W4, "mode": "hospital", "baseline": "typical", "change": "staffing=doh-standard"}
MD, CHARGE, RN = "md-santos", "rn-manalo", "rn-reyes"
WORKER = {"X-Ingest-Key": KEY}


def ask(api: Api, who: str = MD, body: Any = W4, ward: str = "W4") -> Any:
    return api.client.post(f"/wards/{ward}/lab/runs", headers=api.as_(who), json=body)


def runs(api: Api) -> list[dict[str, Any]]:
    return api.sql("SELECT * FROM lab_run ORDER BY id")


def claim(api: Api, headers: dict[str, str] = WORKER) -> Any:
    return api.client.post("/sim/lab/claim", headers=headers)


def report(api: Api, body: Any, run_id: int = 1, headers: dict[str, str] = WORKER) -> Any:
    return api.client.post(f"/sim/lab/runs/{run_id}/report", headers=headers, json=body)


def compare_json(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "compare_format": 1,
        "synthetic": True,
        "mode": "w4",
        "seed": 4,
        "days": 3,
        "baseline": "news2_total_ge_5",
        "change": {"kind": "rule", "name": "news2_chart_trigger", "fields": []},
        "arms": {"baseline": {"alerts": 30}},
    }
    return {**doc, **over}


def done(doc: Any = None, txt: str = "table\nsynthetic, not validated\n") -> dict[str, Any]:
    doc = compare_json() if doc is None else doc
    return {
        "state": "done",
        "compare_json": doc if isinstance(doc, str) else json.dumps(doc),
        "compare_txt": txt,
    }


def running(api: Api, body: Any = W4) -> None:
    assert ask(api, body=body).status_code == 201
    assert claim(api).status_code == 200


# --- the request ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", [MD, CHARGE])
def test_an_md_or_charge_nurse_asks_and_the_run_is_queued(api: Api, who: str) -> None:
    response = ask(api, who)
    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "kind": "compare",
        "requested_by": who,
        "requested_at": "2026-10-14T12:00:00Z",
        **{k: W4[k] for k in ("mode", "seed", "days", "baseline", "change")},
        "state": "queued",
        "claimed_at": None,
        "finished_at": None,
        "reason": None,
    }
    assert audit_of(api, who) == [("lab_request", "lab_run:1", "ok")]


@pytest.mark.parametrize("pair", [(b, c) for b in W4_RULES for c in W4_RULES if b != c][::40])
def test_a_w4_pair_from_the_list_is_accepted(api: Api, pair: tuple[str, str]) -> None:
    assert ask(api, body={**W4, "baseline": pair[0], "change": pair[1]}).status_code == 201


@pytest.mark.parametrize(
    "pair", [(b, c) for b, changes in HOSPITAL_PAIRS.items() for c in changes]
)
def test_each_of_the_nine_hospital_pairs_is_accepted(
    database_url: str, pair: tuple[str, str]
) -> None:
    api = Api(database_url)
    assert ask(api, body={**HOSPITAL, "baseline": pair[0], "change": pair[1]}).status_code == 201


def test_the_lists_are_the_contracts(api: Api) -> None:
    assert len(W4_RULES) == 21 and sum(len(v) for v in HOSPITAL_PAIRS.values()) == 9


BAD_BODIES = {
    "a path": {**W4, "baseline": "scenarios/hospital/x.json"},
    "a parent path": {**W4, "change": "../x"},
    "a shell string": {**W4, "baseline": "news2_total_ge_5; rm -rf /"},
    "a shell substitution": {**W4, "change": "$(id)"},
    "an unknown rule": {**W4, "change": "news2_total_ge_21"},
    "an equal w4 pair": {**W4, "change": W4["baseline"]},
    "a hospital pair outside the nine": {**HOSPITAL, "change": "nurses=typical"},
    "an unknown scenario": {**HOSPITAL, "baseline": "other"},
    "transferOutAfterH": {**HOSPITAL, "change": "transferOutAfterH=doh-standard"},
    "beds": {**HOSPITAL, "change": "beds=doh-standard"},
    "w4 names in hospital mode": {**W4, "mode": "hospital"},
    "a wrong kind": {**W4, "kind": "research"},
    "a missing kind": {k: v for k, v in W4.items() if k != "kind"},
    "a long value": {**W4, "baseline": "x" * 41},
    "seed -1": {**W4, "seed": -1},
    "seed 2147483648": {**W4, "seed": 2147483648},
    "a seed string": {**W4, "seed": "4"},
    "a seed bool": {**W4, "seed": True},
    "days 0": {**W4, "days": 0},
    "days 31": {**W4, "days": 31},
    "an unknown mode": {**W4, "mode": "other"},
    "a run_id": {**W4, "run_id": 1},
    "a patient_id": {**W4, "patient_id": "p-001"},
    "an out path": {**W4, "out": "/tmp/x"},
    "a NUL": {**W4, "change": "news2\x00"},
}


@pytest.mark.parametrize("body", BAD_BODIES.values(), ids=BAD_BODIES.keys())
def test_a_body_outside_the_fixed_lists_is_refused_and_nothing_is_stored(
    api: Api, body: Any
) -> None:
    response = ask(api, body=body)
    assert response.status_code == 422
    assert "x" * 41 not in response.text and "rm -rf" not in response.text
    assert runs(api) == []
    assert audit_of(api, MD) == [("lab_request", "-", "rejected")]


def test_a_body_that_is_not_json_is_refused_and_audited(api: Api) -> None:
    headers = {**api.as_(MD), "Content-Type": "application/json"}
    assert api.client.post("/wards/W4/lab/runs", headers=headers, content="{nope").status_code == 422
    assert audit_of(api, MD) == [("lab_request", "-", "rejected")]


def test_an_rn_may_not_ask_and_an_admin_may_not_ask_or_read(api: Api) -> None:
    assert ask(api, RN).status_code == 403
    assert audit_of(api, RN) == [("lab_request", "W4", "denied")]
    headers = add_admin(api)
    for body in (W4, {"bogus": 1}):
        assert api.client.post("/wards/W4/lab/runs", headers=headers, json=body).status_code == 403
    assert api.client.get("/wards/W4/lab/runs", headers=headers).status_code == 403
    assert api.client.get("/wards/W4/lab/runs/1", headers=headers).status_code == 403
    assert audit_of(api, ADMIN) == [
        ("lab_request", "W4", "denied"),
        ("lab_request", "W4", "denied"),
        ("view_lab_runs", "W4", "denied"),
        ("view_lab_run", "W4", "denied"),
    ]
    assert runs(api) == []


def test_another_ward_is_not_found(api: Api) -> None:
    assert ask(api, ward="W9").status_code == 404
    assert api.client.get("/wards/W9/lab/runs", headers=api.as_(RN)).status_code == 404
    assert api.client.get("/wards/W9/lab/runs/1", headers=api.as_(RN)).status_code == 404
    assert audit_of(api, MD) == [("lab_request", "W9", "not_found")]
    assert audit_of(api, RN) == [
        ("view_lab_runs", "W9", "not_found"),
        ("view_lab_run", "W9", "not_found"),
    ]


# --- reading ---------------------------------------------------------------------------------


def test_every_clinician_role_reads_the_list_and_one_run(api: Api) -> None:
    ask(api)
    for who in (RN, MD, CHARGE):
        listed = api.client.get("/wards/W4/lab/runs", headers=api.as_(who))
        assert listed.status_code == 200 and [r["id"] for r in listed.json()] == [1]
        assert "report" not in listed.json()[0]
        one = api.client.get("/wards/W4/lab/runs/1", headers=api.as_(who))
        assert one.status_code == 200 and one.json()["report"] is None
        assert one.json()["report_text"] is None
    assert audit_of(api, RN) == [("view_lab_runs", "W4", "ok"), ("view_lab_run", "lab_run:1", "ok")]


@pytest.mark.parametrize("lab_run_id", ["999", "0", "01", "abc", "-1", "1.5", "9" * 19, "9" * 30])
def test_a_malformed_or_unknown_id_is_one_audited_not_found(api: Api, lab_run_id: str) -> None:
    response = api.client.get(f"/wards/W4/lab/runs/{lab_run_id}", headers=api.as_(RN))
    assert (response.status_code, response.json()) == (404, {"detail": "Not found"})
    assert audit_of(api, RN) == [("view_lab_run", "W4", "not_found")]


def test_the_list_is_the_newest_twenty(api: Api) -> None:
    write(api, "INSERT INTO clinician (id, role) VALUES ('md-x', 'md')")
    for _ in range(25):
        write(
            api,
            "INSERT INTO lab_run (ward, requested_by, requested_at, kind, mode, seed, days,"
            " baseline, change, state, finished_at, reason) VALUES ('W4', 'md-x', %s, 'compare',"
            " 'w4', 1, 1, 'news2_total_ge_5', 'news2_chart_trigger', 'failed', %s, 'x')",
            api.wall.now,
            api.wall.now,
        )
    ids = [r["id"] for r in api.client.get("/wards/W4/lab/runs", headers=api.as_(RN)).json()]
    assert ids == list(range(25, 5, -1))


# --- limits ----------------------------------------------------------------------------------


def test_one_open_request_for_each_clinician(api: Api) -> None:
    assert ask(api).status_code == 201
    second = ask(api)
    assert (second.status_code, second.json()) == (429, {"detail": "Lab run limit reached"})
    assert ask(api, CHARGE).status_code == 201  # another clinician's own limit
    assert audit_of(api, MD) == [("lab_request", "lab_run:1", "ok"), ("lab_request", "W4", "limited")]
    assert len(runs(api)) == 2
    claim(api)  # running still counts as open
    assert ask(api).status_code == 429
    report(api, {"state": "failed", "reason": "compare did not start"})
    assert ask(api).status_code == 201


def finish_all(api: Api) -> None:
    write(api, "UPDATE lab_run SET state = 'failed', reason = 'x', finished_at = requested_at")


def test_ten_in_a_day_and_the_next_day_is_allowed(api: Api) -> None:
    for _ in range(10):
        assert ask(api).status_code == 201
        finish_all(api)
    assert ask(api).status_code == 429
    api.wall.now += timedelta(hours=24, seconds=1)
    api.tokens.clear()  # a session lasts 12 hours
    assert ask(api).status_code == 201


def test_five_queued_on_the_ward(api: Api) -> None:
    for n in range(5):
        write(api, "INSERT INTO clinician (id, role) VALUES (%s, 'md')", f"md-{n}")
        write(
            api,
            "INSERT INTO lab_run (ward, requested_by, requested_at, kind, mode, seed, days,"
            " baseline, change, state) VALUES ('W4', %s, %s, 'compare', 'w4', 1, 1,"
            " 'news2_total_ge_5', 'news2_chart_trigger', 'queued')",
            f"md-{n}",
            api.wall.now,
        )
    assert ask(api).status_code == 429
    write(api, "UPDATE lab_run SET state = 'failed', reason = 'x', finished_at = requested_at WHERE id = 1")
    assert ask(api).status_code == 201


def test_a_burst_of_requests_passes_the_limit_once(api: Api) -> None:
    from conftest import together

    headers = api.as_(MD)
    codes = together(
        6, lambda _: api.client.post("/wards/W4/lab/runs", headers=headers, json=W4).status_code
    )
    assert sorted(codes) == [201, 429, 429, 429, 429, 429]


# --- the worker: claim -----------------------------------------------------------------------


def test_claim_hands_out_the_oldest_and_then_waits(api: Api) -> None:
    assert claim(api).status_code == 204 and claim(api).content == b""
    ask(api)
    ask(api, CHARGE, HOSPITAL)
    job = claim(api)
    assert job.status_code == 200
    assert job.json() == {"id": 1, **{k: W4[k] for k in ("kind", "mode", "seed", "days")},
                          "baseline": W4["baseline"], "change": W4["change"]}
    assert claim(api).status_code == 204  # one at a time
    assert [r["state"] for r in runs(api)] == ["running", "queued"]
    assert runs(api)[0]["claimed_at"] == api.wall.now
    assert api.sql("SELECT resource FROM audit_log WHERE action = 'lab_claim'") == [
        {"resource": "lab_run:1"}
    ]
    report(api, {"state": "refused", "reason": "compare: nope"})
    assert claim(api).json()["id"] == 2


def test_a_burst_of_claims_hands_out_one(api: Api) -> None:
    from conftest import together

    for who in (MD, CHARGE):
        ask(api, who)
    api.as_(MD)
    codes = together(4, lambda _: claim(api).status_code)
    assert sorted(codes) == [200, 204, 204, 204]


def test_an_empty_poll_writes_no_audit_row(api: Api) -> None:
    claim(api)
    assert api.sql("SELECT * FROM audit_log WHERE action = 'lab_claim'") == []


# --- the worker: report ----------------------------------------------------------------------


def test_a_done_report_is_stored_as_sent(api: Api) -> None:
    running(api)
    txt = "line one\n\nsynthetic, not validated\n"
    assert report(api, done(txt=txt)).status_code == 204
    row = runs(api)[0]
    assert row["state"] == "done" and row["report_text"] == txt and row["reason"] is None
    assert row["report"] == compare_json() and row["finished_at"] == api.wall.now
    seen = api.client.get("/wards/W4/lab/runs/1", headers=api.as_(RN)).json()
    assert seen["report"] == compare_json() and seen["report_text"] == txt
    assert seen["state"] == "done" and seen["finished_at"] == "2026-10-14T12:00:00Z"
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'lab_report'") == [{"outcome": "ok"}]


def test_a_hospital_done_report(api: Api) -> None:
    running(api, HOSPITAL)
    doc = compare_json(
        mode="hospital",
        baseline={"name": "typical"},
        change={"kind": "group", "name": "staffing", "fields": []},
    )
    assert report(api, done(doc)).status_code == 204


@pytest.mark.parametrize(
    "body, state, reason",
    [
        ({"state": "refused", "reason": "compare: change: same values"}, "refused",
         "compare: change: same values"),
        ({"state": "failed", "reason": "compare did not start"}, "failed", "compare did not start"),
    ],
)
def test_a_refused_or_failed_report_is_final(api: Api, body: Any, state: str, reason: str) -> None:
    running(api)
    assert report(api, body).status_code == 204
    row = runs(api)[0]
    assert (row["state"], row["reason"], row["report"]) == (state, reason, None)
    again = report(api, body)
    assert (again.status_code, again.json()) == (409, {"detail": "This lab run is not running"})
    assert runs(api)[0] == row
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'lab_report' ORDER BY seq") == [
        {"outcome": "ok"},
        {"outcome": "rejected"},
    ]


def test_a_report_for_an_unknown_run_is_404_and_for_a_queued_run_409(api: Api) -> None:
    body = {"state": "failed", "reason": "x"}
    assert (report(api, body, 5).status_code, report(api, body, 5).json()) == (
        404,
        {"detail": "Not found"},
    )
    ask(api)
    assert report(api, body).status_code == 409
    assert runs(api)[0]["state"] == "queued"
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'lab_report' ORDER BY seq")[-1] == {
        "outcome": "rejected"
    }


MISMATCHES = {
    "format 2": compare_json(compare_format=2),
    "format true": compare_json(compare_format=True),
    "format missing": {k: v for k, v in compare_json().items() if k != "compare_format"},
    "synthetic false": compare_json(synthetic=False),
    "synthetic 1": compare_json(synthetic=1),
    "mode": compare_json(mode="hospital"),
    "mode missing": {k: v for k, v in compare_json().items() if k != "mode"},
    "a list": [1],
    "a string": "just text",
    "bad json": "{nope",
    "NaN": '{"compare_format": NaN}',
    "a NUL": '{"x": "\\u0000"}',
    "too deep": "[" * 100000,
}


@pytest.mark.parametrize("doc", MISMATCHES.values(), ids=MISMATCHES.keys())
def test_a_done_report_that_does_not_match_fails_the_run(api: Api, doc: Any) -> None:
    running(api)
    response = report(api, done(doc))
    assert (response.status_code, response.json()) == (
        422,
        {"detail": "The report does not match the request"},
    )
    row = runs(api)[0]
    assert (row["state"], row["reason"], row["report"], row["report_text"]) == (
        "failed",
        "the report did not match the request",
        None,
        None,
    )
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'lab_report'") == [
        {"outcome": "rejected"}
    ]


# contract-d7 v3: only the format, the synthetic mark and the mode are checked. The simulator adds
# keys between versions, so a key the API does not know never fails a report.
ACCEPTED = {
    "paired_episodes and the new arm fields": compare_json(
        paired_episodes={"episodes_with_new_alert_under_both": 1, "sd": None},
        arms={"baseline": {"delay_minutes": [85], "delay_median_minutes": 85,
                           "episodes_no_new_alert": 1}},
    ),
    "a key nobody listed": compare_json(some_new_key={"a": [1, 2]}),
    "another seed": compare_json(seed=5),
    "other days": compare_json(days=4),
    "another baseline": compare_json(baseline="news2_total_ge_6"),
    "another change": compare_json(change={"kind": "rule", "name": "news2_total_ge_9"}),
    "no baseline or change": {
        k: v for k, v in compare_json().items() if k not in ("baseline", "change")
    },
}


@pytest.mark.parametrize("doc", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_a_done_report_is_not_refused_for_a_key_the_api_does_not_check(api: Api, doc: Any) -> None:
    running(api)
    assert report(api, done(doc)).status_code == 204
    row = runs(api)[0]
    assert row["state"] == "done" and row["report"] == doc  # stored as it came


HEAD = '{"compare_format":1,"synthetic":true,"mode":"w4","x":'


def nested(depth: int) -> str:  # the object is depth 1, so `depth - 1` arrays hold the key
    return HEAD + "[" * (depth - 1) + "]" * (depth - 1) + "}"


def test_a_document_of_depth_64_is_stored_and_read_back(api: Api) -> None:
    running(api)
    assert report(api, done(nested(64))).status_code == 204
    assert api.client.get("/wards/W4/lab/runs/1", headers=api.as_(MD)).status_code == 200


@pytest.mark.parametrize("doc", [nested(65), HEAD + "[" * 256 + "]" * 256 + "}", nested(300)])
def test_a_deeper_document_is_a_mismatch(api: Api, doc: str) -> None:
    running(api)
    assert report(api, done(doc)).status_code == 422
    assert runs(api)[0]["state"] == "failed"


@pytest.mark.parametrize("value", ["1e400", '"\\ud800"', "-1e999"])
def test_an_infinity_or_a_lone_surrogate_in_compare_json_is_a_mismatch(api: Api, value: str) -> None:
    running(api)
    response = report(api, done(HEAD + value + "}"))
    assert (response.status_code, response.json()) == (
        422,
        {"detail": "The report does not match the request"},
    )
    row = runs(api)[0]
    assert (row["state"], row["report"]) == ("failed", None)
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'lab_report'") == [
        {"outcome": "rejected"}
    ]


def test_an_empty_compare_txt_is_a_mismatch(api: Api) -> None:
    running(api)
    response = report(api, done(txt=""))
    assert (response.status_code, response.json()) == (
        422,
        {"detail": "The report does not match the request"},
    )
    assert runs(api)[0]["state"] == "failed"


def raw(api: Api, text: str) -> Any:  # a JSON library may refuse to build these bodies
    return api.client.post(
        "/sim/lab/runs/1/report",
        headers={**WORKER, "Content-Type": "application/json"},
        content=text.encode("ascii"),
    )


BAD_BODIES = [
    '{"state":"failed","reason":"x\\ud800"}',
    '{"state":"done","compare_json":"{}","compare_txt":"x\\ud800"}',
    *(
        f'{{"state":"{s}","reason":"a{c}b"}}'
        for s in ("failed", "refused")
        for c in ("\\u0085", "\\u2028", "\\u2029", "\\u202e", "\\u2066", "\\u009f", "\\r", "\\n")
    ),
    *(
        f'{{"state":"done","compare_json":"{{}}","compare_txt":"a{c}b"}}'
        for c in ("\\u0085", "\\u2028", "\\u2029", "\\u202e", "\\u2069", "\\u009f", "\\r", "\\u007f")
    ),
]


@pytest.mark.parametrize("text", BAD_BODIES)
def test_a_lone_surrogate_or_a_line_breaking_character_changes_nothing(api: Api, text: str) -> None:
    running(api)
    before = runs(api)
    assert raw(api, text).status_code == 422
    assert runs(api) == before


def test_lf_is_allowed_in_compare_txt(api: Api) -> None:
    running(api)
    assert report(api, done(txt="one\ntwo\n\nthree")).status_code == 204
    assert runs(api)[0]["report_text"] == "one\ntwo\n\nthree"


def test_a_hospital_run_reported_as_w4_fails(api: Api) -> None:
    running(api, HOSPITAL)
    wrong = compare_json(
        mode="w4", baseline={"name": "typical"}, change={"kind": "group", "name": "staffing"}
    )
    assert report(api, done(wrong)).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"state": "done", "compare_json": "x" * 524289, "compare_txt": "t"},
        {"state": "done", "compare_json": "{}", "compare_txt": "t" * 131073},
        {"state": "done", "compare_json": "{}", "compare_txt": "a\rb"},
        {"state": "done", "compare_json": "{}", "compare_txt": "a\tb"},
        {"state": "done", "compare_json": "{}", "compare_txt": "a\x7fb"},
        {"state": "failed", "reason": "one\nline"},
        {"state": "failed", "reason": "one\rline"},
        {"state": "refused", "reason": ""},
        {"state": "refused", "reason": "x" * 301},
        {"state": "failed"},
        {"state": "refused", "reason": "ok", "extra": 1},
        {"state": "done", "compare_json": "{}", "compare_txt": "t", "reason": "x"},
        {"state": "queued"},
        {"compare_json": "{}", "compare_txt": "t"},
    ],
)
def test_a_report_that_breaks_the_shape_or_a_cap_changes_nothing(api: Api, body: Any) -> None:
    running(api)
    before = runs(api)
    assert report(api, body).status_code == 422
    assert runs(api) == before
    assert report(api, "not an object").status_code == 422


def test_the_largest_allowed_report_is_accepted(api: Api) -> None:
    running(api)
    pad = "x" * 100
    doc = json.dumps(compare_json(pad=pad))
    assert report(api, done(doc, txt="t" * 131072)).status_code == 204


def test_a_report_id_outside_the_database_range_is_422(api: Api) -> None:
    body = {"state": "failed", "reason": "x"}
    assert report(api, body, 0).status_code == 422
    assert report(api, body, 2**63).status_code == 422


# --- claim against a timeout --------------------------------------------------------------------


def test_the_claim_statement_cannot_start_a_row_that_is_no_longer_queued(api: Api) -> None:
    """Two connections: one fails the queued row and has not committed; the claim blocks on that
    row, then runs after the commit. Without `state = 'queued'` it would set the failed row running."""
    assert ask(api).status_code == 201
    result: list[Any] = []
    with connect(api.url) as first, connect(api.url) as second:
        first.execute("BEGIN")
        first.execute("UPDATE lab_run SET state = 'failed', reason = 'x', finished_at = now()")

        def claim_now() -> None:
            second.execute("BEGIN")
            result.append(second.execute(CLAIM, (api.wall(),)).fetchall())
            second.execute("COMMIT")

        thread = threading.Thread(target=claim_now)
        thread.start()
        thread.join(0.5)
        assert thread.is_alive()  # waiting for the lock
        first.execute("COMMIT")
        thread.join(10)
    assert result == [[]]
    assert runs(api)[0]["state"] == "failed"


# --- timeouts ----------------------------------------------------------------------------------


def stale(api: Api, hours: float) -> None:
    api.wall.now += timedelta(hours=hours)


@pytest.mark.parametrize("via", ["list", "get", "create", "claim", "report"])
def test_a_queued_run_not_claimed_within_an_hour_fails(api: Api, via: str) -> None:
    ask(api)
    stale(api, 1.01)
    rn = api.as_(RN)
    if via == "list":
        api.client.get("/wards/W4/lab/runs", headers=rn)
    elif via == "get":
        api.client.get("/wards/W4/lab/runs/1", headers=rn)
    elif via == "create":
        assert ask(api).status_code == 201  # the first no longer counts as open
    elif via == "claim":
        assert claim(api).status_code == 204
    else:
        assert report(api, {"state": "failed", "reason": "x"}).status_code == 409
    row = runs(api)[0]
    assert (row["state"], row["reason"]) == ("failed", "no worker claimed it within an hour")
    assert row["finished_at"] == api.wall.now


@pytest.mark.parametrize("via", ["list", "get", "create", "claim", "report"])
def test_a_running_run_not_reported_within_twenty_minutes_fails(api: Api, via: str) -> None:
    running(api)
    stale(api, 21 / 60)
    rn = api.as_(RN)
    if via == "list":
        api.client.get("/wards/W4/lab/runs", headers=rn)
    elif via == "get":
        api.client.get("/wards/W4/lab/runs/1", headers=rn)
    elif via == "create":
        assert ask(api).status_code == 201
    elif via == "claim":
        assert claim(api).status_code == 204  # the queue is empty, and nothing is running
    else:
        done_body = done()
        assert report(api, done_body).status_code == 409
    row = runs(api)[0]
    assert (row["state"], row["reason"]) == ("failed", "the worker did not report within 20 minutes")


def test_a_run_inside_its_limits_is_left_alone(api: Api) -> None:
    ask(api)
    stale(api, 0.9)
    claim(api)
    stale(api, 0.3)
    api.client.get("/wards/W4/lab/runs", headers=api.as_(RN))
    assert runs(api)[0]["state"] == "running"


def test_a_claim_after_a_timeout_hands_out_the_next(api: Api) -> None:
    running(api)
    ask(api, CHARGE)
    stale(api, 21 / 60)
    assert claim(api).json()["id"] == 2


# --- the worker routes need the ingest key -----------------------------------------------------

WORKER_ROUTES = [("/sim/lab/claim", None), ("/sim/lab/runs/1/report", {"state": "failed", "reason": "x"})]


@pytest.mark.parametrize("path, body", WORKER_ROUTES)
@pytest.mark.parametrize("key", [None, "wrong", KEY[:-1], KEY + "x"])
def test_the_worker_routes_refuse_a_missing_or_wrong_key(
    api: Api, path: str, body: Any, key: str | None
) -> None:
    ask(api)
    headers = {} if key is None else {"X-Ingest-Key": key}
    response = api.client.post(path, headers=headers, json=body)
    assert (response.status_code, response.json()) == (403, {"detail": "Forbidden"})
    assert runs(api)[0]["state"] == "queued"
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'ingest'") == [{"outcome": "denied"}]


@pytest.mark.parametrize("path, body", WORKER_ROUTES)
def test_the_worker_routes_do_not_accept_a_bearer_token(api: Api, path: str, body: Any) -> None:
    ask(api)
    for who in (MD, RN, CHARGE):
        response = api.client.post(path, headers=api.as_(who), json=body)
        assert response.status_code == 403
    assert runs(api)[0]["state"] == "queued"


@pytest.mark.parametrize("path, body", WORKER_ROUTES)
def test_the_worker_routes_fail_closed_with_no_key_configured(
    database_url: str, path: str, body: Any
) -> None:
    unset = Api(database_url, ingest_key=None)
    for key in (None, "", "anything"):
        headers = {} if key is None else {"X-Ingest-Key": key}
        response = unset.client.post(path, headers=headers, json=body)
        assert (response.status_code, response.json()) == (503, {"detail": "Ingest is not configured"})
    assert runs(unset) == []


def test_the_clinician_routes_do_not_accept_the_ingest_key(api: Api) -> None:
    for call in (
        lambda: api.client.get("/wards/W4/lab/runs", headers=WORKER),
        lambda: api.client.get("/wards/W4/lab/runs/1", headers=WORKER),
        lambda: api.client.post("/wards/W4/lab/runs", headers=WORKER, json=W4),
    ):
        assert call().status_code == 401


# --- nothing from a report reaches another table -----------------------------------------------


def test_a_report_writes_only_its_own_row(api: Api) -> None:
    running(api)
    with connect(api.url) as conn:
        before = [
            ln for ln in dump_database(conn).splitlines()
            if not ln.startswith(("lab_run:", "audit_log:", "session:"))
        ]
    report(api, done(txt="note: p-001 is at risk\n"))
    with connect(api.url) as conn:
        after = [
            ln for ln in dump_database(conn).splitlines()
            if not ln.startswith(("lab_run:", "audit_log:", "session:"))
        ]
    assert before == after


# --- the table's own checks ------------------------------------------------------------------


def insert(api: Api, **over: Any) -> None:
    row: dict[str, Any] = {
        "ward": "W4",
        "requested_by": MD,
        "requested_at": api.wall.now,
        "kind": "compare",
        "mode": "w4",
        "seed": 1,
        "days": 1,
        "baseline": "news2_total_ge_5",
        "change": "news2_chart_trigger",
        "state": "queued",
        **over,
    }
    write(
        api,
        f"INSERT INTO lab_run ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})",
        *row.values(),
    )


@pytest.mark.parametrize(
    "over",
    [
        {"kind": "research"},
        {"mode": "other"},
        {"seed": -1},
        {"seed": 2147483648},
        {"days": 0},
        {"days": 31},
        {"baseline": "scenarios/x.json"},
        {"baseline": "a b"},
        {"change": "../x"},
        {"change": "x" * 41},
        {"state": "other"},
        {"ward": ""},
        {"requested_by": "nobody"},
        {"state": "failed"},  # no reason, no finished_at
        {"state": "failed", "reason": ""},
        {"state": "queued", "reason": "x"},
        {"state": "running"},  # no claimed_at
        {"claimed_at": "2026-10-14T12:00:00Z"},  # queued with claimed_at
        {"state": "queued", "finished_at": "2026-10-14T12:00:00Z"},
        {"state": "failed", "reason": "x", "finished_at": "2026-10-14T12:00:00Z", "report_text": "t"},
    ],
)
def test_the_table_refuses_a_row_that_breaks_a_check(api: Api, over: dict[str, Any]) -> None:
    with pytest.raises(psycopg.errors.Error):
        insert(api, **over)


def test_the_table_refuses_a_done_row_with_no_report(api: Api) -> None:
    at = api.wall.now
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(api, state="done", claimed_at=at, finished_at=at, report_text="t")
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(api, state="done", claimed_at=at, finished_at=at)

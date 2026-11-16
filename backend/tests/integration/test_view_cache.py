"""The view cache (BIOSENTRY_VIEW_CACHE_SECONDS): a ward view, a public open view and a bed card are
kept for a few seconds. What it must never do: serve an answer built for one scope to a reader of another,
serve an answer older than the time, hide a write this process handled, or skip an audit row (I7)."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_overview import ADMIN, add_admin, write
from test_read_window import long_stay
from test_store import Api, api  # noqa: F401 (api: fixture)
from test_units import PATIENTS, hospital, stay

from biosentry.api.app import create_app
from biosentry.core.audit import AuditLog

TTL = 3.0
WARD = [*PATIENTS, stay("w-2", "W4", "rn-reyes", None, "4B")]  # W4: rn-delacruz sees w-1, rn-reyes w-2
READERS = ("md-santos", "rn-delacruz", "rn-reyes", "cn-er", "cn-icu", "cn-med3a")


def clients(api: Api) -> tuple[TestClient, TestClient]:
    """The same database through an API with no cache and one with it (the open view on in both)."""
    on = replace(api.settings, open_ward_view=True)
    plain = TestClient(create_app(on, clock=api.wall))
    kept = TestClient(create_app(replace(on, view_cache_seconds=TTL), clock=api.wall))
    return plain, kept


def body(client: TestClient, path: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    r = client.get(path, headers=headers or {})
    return r.status_code, r.json()


def ids(rows: dict[str, Any]) -> list[tuple[str, int]]:
    return [(r["id"], r["rank"]) for r in rows["ranked"]]


def test_every_reader_gets_the_same_answer_with_the_cache_on_cold_and_warm(api: Api) -> None:
    hospital(api, WARD)
    admin = add_admin(api)
    plain, kept = clients(api)
    paths = ["/wards", *(f"/wards/{u}/rows" for u in ("W4", "ER", "ICU", "MED3A")), "/patients/w-1", "/patients/e-1", "/patients/w-2"]
    for who in (*READERS, None):
        head = admin if who is None else api.as_(who)
        for path in paths:
            expected = body(plain, path, head)
            assert body(kept, path, head) == expected, (who, path)  # cold
            assert body(kept, path, head) == expected, (who, path)  # warm
    for path in ("/open-view", "/wards/W4/open-view", "/wards/ER/open-view", "/wards/W4/open-view/beds/4A", "/wards/W4/open-view/beds/9Z"):
        expected = body(plain, path)
        assert body(kept, path) == expected and body(kept, path) == expected, path


def test_an_answer_built_for_one_scope_is_never_served_to_another(api: Api) -> None:
    hospital(api, WARD)
    admin = add_admin(api)
    plain, kept = clients(api)
    everyone = body(kept, "/wards/W4/rows", admin)[1]  # the admin's answer is now kept
    assert sorted(ids(everyone)) and {i for i, _ in ids(everyone)} == {"w-1", "w-2"}
    for who, only in (("rn-delacruz", "w-1"), ("rn-reyes", "w-2")):
        mine = body(kept, "/wards/W4/rows", api.as_(who))[1]
        assert [i for i, _ in ids(mine)] == [only] and ids(mine) == [(only, 1)]  # own set, own ranks
        assert body(plain, "/wards/W4/rows", api.as_(who))[1] == mine
    assert body(kept, "/patients/w-2", api.as_("rn-delacruz"))[0] == 404  # not theirs, whatever is kept
    assert body(kept, "/wards/W4/rows", admin)[1] == everyone


def test_an_answer_is_never_older_than_the_time(api: Api) -> None:
    hospital(api, WARD)
    admin = add_admin(api)
    _, kept = clients(api)
    first = body(kept, "/wards/W4/rows", admin)[1]
    bed = {r["id"]: r["bed"] for r in first["ranked"]}["w-1"]
    write(api, "UPDATE patient SET bed = 'ZZ9' WHERE id = 'w-1'")  # a change this process did not see happen
    api.wall.now += timedelta(seconds=TTL - 0.1)
    assert {r["id"]: r["bed"] for r in body(kept, "/wards/W4/rows", admin)[1]["ranked"]}["w-1"] == bed  # still the kept one
    api.wall.now += timedelta(seconds=0.2)  # older than the time: built again
    assert {r["id"]: r["bed"] for r in body(kept, "/wards/W4/rows", admin)[1]["ranked"]}["w-1"] == "ZZ9"


def test_a_write_this_process_handles_ends_the_kept_answers_at_once(api: Api) -> None:
    hospital(api, WARD)
    nurse = api.as_("rn-delacruz")
    plain, kept = clients(api)
    before = body(kept, "/wards/W4/rows", nurse)[1]
    assert kept.post("/patients/w-1/vitals", json={"systolic": 200, "diastolic": 120}, headers=nurse).status_code in (200, 201)
    after = body(kept, "/wards/W4/rows", nurse)[1]
    assert after != before and after == body(plain, "/wards/W4/rows", nurse)[1]


def test_a_sim_batch_ends_the_answers_even_when_another_process_took_it(api: Api) -> None:
    run_id = hospital(api, WARD)
    nurse = api.as_("rn-delacruz")
    _, kept = clients(api)
    first = body(kept, "/wards/W4/rows", nurse)[1]
    # the batch goes through a different app (another process): this one's cache is not told
    other = TestClient(create_app(api.settings, clock=api.wall))
    from test_store import batch  # noqa: PLC0415

    sent = other.post(f"/sim/runs/{run_id}/batch", json=batch(minute=15, patients=[], observations=[], bins=[], events=[]), headers={"X-Ingest-Key": "test-ingest-key"})
    assert sent.status_code == 204
    assert body(kept, "/wards/W4/rows", nurse)[1] != first  # the run's clock moved: a new key, built again


def test_every_view_from_the_cache_still_goes_through_the_audit(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    hospital(api, WARD)
    admin = add_admin(api)
    _, kept = clients(api)
    calls: list[tuple[str, str]] = []
    real = AuditLog.append_view

    def spy(self: AuditLog, who: str, action: str, resource: str) -> Any:
        calls.append((action, resource))
        return real(self, who, action, resource)

    monkeypatch.setattr(AuditLog, "append_view", spy)
    for _ in range(3):
        assert kept.get("/wards/W4/rows", headers=admin).status_code == 200
        assert kept.get("/patients/w-1", headers=admin).status_code == 200
    assert calls == [("view_ward", "W4"), ("view_patient", "w-1")] * 3  # the 2nd and 3rd are from the cache
    assert api.sql("SELECT action FROM audit_log WHERE action IN ('view_ward', 'view_patient') ORDER BY seq")  # and rows are written


def test_the_public_views_are_kept_whole_and_end_with_the_time(api: Api) -> None:
    hospital(api, WARD)
    _, kept = clients(api)
    unit, card = body(kept, "/wards/W4/open-view"), body(kept, "/wards/W4/open-view/beds/4A")
    write(api, "UPDATE patient SET bed = 'ZZ9' WHERE id = 'w-1'")
    assert body(kept, "/wards/W4/open-view") == unit and body(kept, "/wards/W4/open-view/beds/4A") == card
    api.wall.now += timedelta(seconds=TTL + 0.1)
    assert body(kept, "/wards/W4/open-view") != unit
    assert body(kept, "/wards/W4/open-view/beds/4A")[0] == 404  # the bed is gone: "no such bed", kept as an answer too
    assert body(kept, "/wards/W4/open-view/beds/4A")[0] == 404


def test_an_entry_holds_the_rows_and_only_the_one_state_a_page_asked_for_never_a_ward_of_histories(api: Api) -> None:
    hospital(api, [long_stay("w-1", "W4", "rn-delacruz", "md-santos", "4A"), long_stay("w-2", "W4", "rn-reyes", None, "4B"), *PATIENTS[1:]])
    admin = add_admin(api)
    _, kept = clients(api)
    def items() -> dict[Any, Any]:
        return dict(kept.app.state.view_cache._items)

    assert kept.get("/wards/W4/rows", headers=admin).status_code == 200
    [(_, (_, (ranked, abstained, states)))] = items().items()
    assert len(ranked) + len(abstained) == 2 and states == {}  # the rows only: no state, no week of bins
    assert kept.get("/patients/w-1", headers=admin).status_code == 200
    held = [v[1][2] for v in items().values() if v[1][2]]
    assert [set(s) for s in held] == [{"w-1"}] and len(held[0]["w-1"].vital_history.heart_rate) > 12  # the page's own patient
    assert len(items()) <= 16


def test_the_same_patients_for_two_roles_share_one_entry_and_differ_only_by_what_is_added_after_it(api: Api) -> None:
    hospital(api, [*PATIENTS, stay("e-2", "ER", "rn-reyes", "md-santos", "E2")])
    admin, charge = add_admin(api), api.as_("cn-er")  # both may see exactly the ER's two patients
    plain, kept = clients(api)
    as_admin = body(kept, "/wards/ER/rows", admin)[1]
    assert len(kept.app.state.view_cache._items) == 1  # the admin's answer is now kept
    as_charge = body(kept, "/wards/ER/rows", charge)[1]
    assert len(kept.app.state.view_cache._items) == 1  # the charge nurse's read was served from it: no second entry
    for part in ("ranked", "abstained", "ward", "kind", "tier_status"):
        assert as_charge[part] == as_admin[part], part  # the shared part: the same bytes
    assert {k for k in as_admin if as_admin[k] != as_charge[k]} == {"clinician"}  # what is added after the cache
    assert (as_admin["clinician"], as_charge["clinician"]) == (ADMIN, "cn-er")
    assert as_admin == body(plain, "/wards/ER/rows", admin)[1] and as_charge == body(plain, "/wards/ER/rows", charge)[1]

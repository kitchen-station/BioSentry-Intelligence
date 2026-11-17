"""Private patient history: current scope, owner, exact retention cut, and explicit run links."""

from datetime import timedelta

import pytest

from test_lab import MD, ask, claim, compare_json, done, report
from test_overview import ADMIN, add_admin, audit_of, write
from test_patient_ask import answered, claimed, listing, one, post_report, put
from test_store import Api, api, sim_patient, start  # noqa: F401
from test_units import hospital


def history(api: Api, pid: str = "p-001", who: str = MD):
    return api.client.get(f"/patients/{pid}/assistant/history", headers=api.as_(who))


def runs(api: Api, pid: str = "p-001", who: str = MD, suffix: str = ""):
    return api.client.get(f"/patients/{pid}/lab/runs{suffix}", headers=api.as_(who))


def test_messages_are_saved_once_with_the_asker_and_stay(api: Api) -> None:
    job, sha = claimed(api)
    assert [r["role"] for r in history(api).json()] == ["user"]
    assert post_report(api, answered(sha, "Synthetic answer"), job["id"]).status_code == 204
    assert post_report(api, answered(sha), job["id"]).status_code == 409
    messages = history(api).json()
    assert [r["role"] for r in messages] == ["user", "assistant"]
    assert messages[1]["text"] == "Synthetic answer"
    assert {r["lab_run_id"] for r in messages} == {job["id"]}
    assert history(api, who="rn-delacruz").json() == []  # same patient, different owner
    assert history(api, pid="p-002").json() == []
    assert api.sql("SELECT DISTINCT user_id, run_id, patient_id, scope FROM assistant_message") == [
        {"user_id": MD, "run_id": 1, "patient_id": "p-001", "scope": "patient"}
    ]
    history(api)
    history(api)
    assert audit_of(api, MD)[-2:] == [("view_assistant_history", "p-001", "ok")] * 2


def test_exact_90_day_cut_and_legacy_question_reads(api: Api) -> None:
    job, sha = claimed(api)
    assert post_report(api, answered(sha)).status_code == 204
    cutoff = api.wall.now - timedelta(days=90)
    write(api, "UPDATE assistant_message SET created_at = %s WHERE role = 'user'", cutoff - timedelta(microseconds=1))
    write(api, "UPDATE assistant_message SET created_at = %s WHERE role = 'assistant'", cutoff)
    assert [m["role"] for m in history(api).json()] == ["assistant"]
    assert len(api.sql("SELECT * FROM assistant_message")) == 1  # stale content pruned
    write(api, "UPDATE lab_run SET requested_at = %s WHERE id = %s", cutoff, job["id"])
    assert len(listing(api, MD, "p-001").json()) == 1
    assert one(api, MD, "p-001", job["id"]).status_code == 200
    write(api, "UPDATE lab_run SET requested_at = %s WHERE id = %s", cutoff - timedelta(microseconds=1), job["id"])
    assert listing(api, MD, "p-001").json() == []
    assert one(api, MD, "p-001", job["id"]).status_code == 404


@pytest.mark.parametrize("pid", ["p-003", "p-missing"])
def test_missing_and_unassigned_are_identical_and_audited(api: Api, pid: str) -> None:
    for response in (history(api, pid), runs(api, pid), runs(api, pid, suffix="/1")):
        assert response.status_code == 404 and response.json() == {"detail": "Not found"}
    assert audit_of(api, MD) == [(action, pid, "not_found") for action in (
        "view_assistant_history", "view_patient_lab_runs", "view_patient_lab_run"
    )]


def test_revocation_departure_and_reused_ids_hide_history(api: Api) -> None:
    put(api, MD, "p-001")
    write(api, "DELETE FROM care_assignment WHERE patient_id = 'p-001' AND clinician_id = %s", MD)
    assert history(api).status_code == runs(api).status_code == 404
    write(api, "UPDATE patient SET left_at = %s, outcome = 'discharged' WHERE id = 'p-006'", api.wall.now)
    assert history(api, "p-006").status_code == runs(api, "p-006").status_code == 404
    start(api, patients=[sim_patient("p-001", md=MD)])
    assert history(api).json() == []


def test_admin_uses_patient_page_scope_and_sees_no_other_persons_messages(api: Api) -> None:
    put(api, MD, "p-001")
    add_admin(api)
    assert history(api, who=ADMIN).json() == []
    assert runs(api, who=ADMIN).status_code == 403  # a lab row says who asked what: closed to the admin, as the ward lab routes are
    assert runs(api, suffix="/1", who=ADMIN).status_code == 403
    assert ("view_patient_lab_runs", "p-001", "denied") in audit_of(api, ADMIN)
    assert history(api, "p-005", who=ADMIN).status_code == 200  # not ranked still visible


def linked(api: Api, refs: list[dict] | None = None) -> int:
    made = ask(api)
    assert made.status_code == 201
    rid = made.json()["id"]
    assert claim(api).status_code == 200
    doc = compare_json(**({} if refs is None else {"patient_refs": refs}))
    assert report(api, done(doc), rid).status_code == 204
    return rid


def test_simulated_runs_are_explicit_newest_first_read_only_and_stay_scoped(api: Api) -> None:
    first = linked(api, [{"run_id": 1, "patient_id": "p-001"}])
    second = linked(api, [{"run_id": 1, "patient_id": "p-001"}])
    unlinked = linked(api)
    assert [r["id"] for r in runs(api).json()] == [second, first]
    assert runs(api, "p-002").json() == []
    detail = runs(api, suffix=f"/{first}").json()
    assert detail["report"]["synthetic"] is True
    assert "patient_refs" not in detail["report"]  # the other patients' ids stay out
    for rid in ("bad", "0", "999", str(unlinked)):
        assert runs(api, suffix=f"/{rid}").status_code == 404
    assert runs(api, "p-002", suffix=f"/{first}").status_code == 404
    before = api.sql("SELECT * FROM lab_run ORDER BY id")
    runs(api)
    assert api.sql("SELECT * FROM lab_run ORDER BY id") == before
    start(api, patients=[sim_patient("p-001", md=MD)])
    assert runs(api).json() == []
    assert runs(api, suffix=f"/{first}").status_code == 404


@pytest.mark.parametrize("refs", [None, {}, ["p-001"], [{"run_id": True, "patient_id": "p-001"}],
    [{"run_id": 1, "patient_id": "missing"}], [{"run_id": 1, "patient_id": "p-001", "extra": 1}]])
def test_invalid_patient_links_reject_the_report(api: Api, refs) -> None:
    ask(api)
    claim(api)
    assert report(api, done(compare_json(patient_refs=refs))).status_code == 422
    assert runs(api).json() == []


def test_links_and_reads_do_not_cross_units(api: Api) -> None:
    run_id = hospital(api)
    ask(api)
    claim(api)
    assert report(api, done(compare_json(patient_refs=[{"run_id": run_id, "patient_id": "e-1"}]))).status_code == 422
    assert history(api, "e-1").status_code == runs(api, "e-1").status_code == 404


def test_failed_answer_does_not_store_an_assistant_message(api: Api) -> None:
    _, sha = claimed(api)
    assert post_report(api, answered(sha, grounding="no")).status_code == 422
    assert [m["role"] for m in history(api).json()] == ["user"]


def test_anonymous_reads_are_refused(api: Api) -> None:
    for suffix in ("assistant/history", "lab/runs", "lab/runs/1"):
        assert api.client.get(f"/patients/p-001/{suffix}").status_code == 401

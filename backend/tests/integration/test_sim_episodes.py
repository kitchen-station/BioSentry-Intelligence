"""POST /sim/runs/{id}/episodes (contract-truth v1.1): the simulator's episode truth, and its one read, the
history route. A run is posted as the simulator posts it, then read back through the API."""

import json
from typing import Any

import pytest
from test_baseline_route import served
from test_history_route import SICK, get, history, minute, sick_bins
from test_model_inputs import stay
from test_overview import ADMIN, add_admin
from test_store import KEY, Api, api, batch, sim_patient, start  # noqa: F401 (api: fixture)

from biosentry.db.migrate import migrations
from biosentry.repositories.postgres import connect

NOW_MINUTE = 300  # the run's clock once `live` has posted its batch


def ep(pid: str = "s-1", kind: str = "respiratory", start_minute: int = 135, full_minute: int = 150) -> dict[str, Any]:
    return {"patient_id": pid, "kind": kind, "start_minute": start_minute, "full_minute": full_minute}


def live(api: Api, patients: list[dict[str, Any]] | None = None, at: int = NOW_MINUTE) -> int:
    """A run with stays of 20 quiet bins, whose newest batch is at this run minute."""
    run_id = start(api, patients=patients or [stay("s-1", 20)])
    quiet = batch(minute=at, patients=[], observations=[], bins=[], events=[])
    assert api.post(f"/sim/runs/{run_id}/batch", quiet).status_code == 204
    return run_id


def put(api: Api, run_id: int, *episodes: dict[str, Any], key: str | None = KEY) -> Any:
    return api.post(f"/sim/runs/{run_id}/episodes", {"episodes": list(episodes)}, key=key)


def stored(api: Api) -> list[dict[str, Any]]:
    return api.sql("SELECT * FROM sim_episode ORDER BY run_id, patient_id")


# --- The route ---------------------------------------------------------------------------------


@pytest.mark.parametrize("key", [None, "wrong", KEY[:-1], KEY + "x"])
def test_a_wrong_or_missing_key_is_403_whatever_the_run_and_the_body(api: Api, key: str | None) -> None:
    run_id = live(api)
    assert put(api, run_id, ep(), key=key).status_code == 403
    assert put(api, 999, ep(), key=key).status_code == 403  # an unknown run tells nothing
    assert api.post(f"/sim/runs/{run_id}/episodes", {"episodes": [], "x": 1}, key=key).status_code == 403
    assert api.post(f"/sim/runs/{run_id}/episodes", "nonsense", key=key).status_code == 403
    assert stored(api) == []


def test_a_key_that_is_not_set_is_503(database_url: str) -> None:
    api = Api(database_url, ingest_key=None)
    for key in (None, "", "anything"):
        assert put(api, 1, ep(), key=key).status_code == 503
    assert stored(api) == []


def test_an_unknown_run_is_404(api: Api) -> None:
    live(api)
    assert put(api, 999, ep()).status_code == 404
    assert stored(api) == []


def test_a_run_that_is_not_marked_synthetic_is_409_and_stores_nothing(api: Api) -> None:
    (fixture,) = api.sql("SELECT id, source FROM run")
    assert fixture["source"] == "fixture"
    assert put(api, fixture["id"], ep("p-001", start_minute=0, full_minute=0)).status_code == 409
    assert stored(api) == []


def test_a_run_that_a_newer_one_replaced_is_409_as_the_other_posts_are(api: Api) -> None:
    old = live(api)
    live(api)
    assert put(api, old, ep()).status_code == 409
    assert stored(api) == []


BAD = {
    "an empty list": {"episodes": []},
    "no list": {},
    "a list that is not": {"episodes": ep()},
    "a key on the body": {"episodes": [ep()], "note": "x"},
    "a key on an episode": {"episodes": [{**ep(), "ramp": 3}]},
    "a missing field": {"episodes": [{k: v for k, v in ep().items() if k != "full_minute"}]},
    "a kind outside the three": {"episodes": [ep(kind="afib")]},
    "a kind in capitals": {"episodes": [ep(kind="Respiratory")]},
    "a kind that is a list": {"episodes": [ep(kind=["respiratory"])]},
    "a start below 0": {"episodes": [ep(start_minute=-1, full_minute=5)]},
    "a full before the start": {"episodes": [ep(start_minute=135, full_minute=134)]},
    "a start that is text": {"episodes": [ep(start_minute="135")]},
    "a start that is a float": {"episodes": [ep(start_minute=135.0)]},
    "a start that is a bool": {"episodes": [ep(start_minute=True, full_minute=5)]},
    "a patient that is not in the run": {"episodes": [ep("s-9")]},
    "one good and one patient not in the run": {"episodes": [ep(), ep("s-9")]},
    "an empty patient id": {"episodes": [ep("")]},
    "two for one patient": {"episodes": [ep(), ep(kind="tachycardic")]},
    "a start 16 minutes past the newest": {"episodes": [ep(start_minute=NOW_MINUTE + 16, full_minute=NOW_MINUTE + 20)]},
    "one start too far among good ones": {"episodes": [ep(), ep("s-2", start_minute=NOW_MINUTE + 16, full_minute=400)]},
    "201 entries": {"episodes": [ep(f"s-{i}") for i in range(201)]},
}


@pytest.mark.parametrize("name", BAD)
def test_a_body_that_fails_a_check_is_422_and_nothing_is_stored(api: Api, name: str) -> None:
    run_id = live(api, [stay("s-1", 2), stay("s-2", 2)])
    assert api.post(f"/sim/runs/{run_id}/episodes", BAD[name]).status_code == 422
    assert stored(api) == []


def test_the_whole_body_is_refused_when_one_entry_is_not_good(api: Api) -> None:
    run_id = live(api, [stay("s-1", 2), stay("s-2", 2)])
    assert put(api, run_id, ep("s-1"), ep("s-2", start_minute=NOW_MINUTE + 16, full_minute=400)).status_code == 422
    assert stored(api) == []


def test_an_accepted_post_is_204_stores_the_four_fields_and_two_patients_in_one_body(api: Api) -> None:
    run_id = live(api, [stay("s-1", 2), stay("s-2", 2)])
    before = api.sql("SELECT max(seq) AS s FROM audit_log")[0]["s"] or 0
    response = put(api, run_id, ep("s-1"), ep("s-2", "hypotensive", 200, 260))
    assert (response.status_code, response.content) == (204, b"")
    rows = [{k: r[k] for k in ("run_id", "patient_id", "kind", "start_minute", "full_minute")} for r in stored(api)]
    assert rows == [
        {"run_id": run_id, "patient_id": "s-1", "kind": "respiratory", "start_minute": 135, "full_minute": 150},
        {"run_id": run_id, "patient_id": "s-2", "kind": "hypotensive", "start_minute": 200, "full_minute": 260},
    ]  # fmt: skip
    assert {r["received_at"] for r in stored(api)} == {api.wall.now}
    # One audit row for the accepted post: the run's id only, no patient, no kind, no minute.
    written = api.sql("SELECT clinician_id, role, action, resource, outcome FROM audit_log WHERE seq > %s", before)
    assert written == [
        {"clinician_id": None, "role": None, "action": "ingest_episodes", "resource": f"run:{run_id}", "outcome": "ok"}
    ]
    assert put(api, run_id, ep("s-1"), ep("s-2", "hypotensive", 200, 260)).status_code == 204  # the same again
    assert len(api.sql("SELECT 1 FROM audit_log WHERE seq > %s", before)) == 2  # each accepted post has its row
    assert put(api, run_id, ep("nobody")).status_code == 422
    assert len(api.sql("SELECT 1 FROM audit_log WHERE seq > %s", before)) == 2  # a refused body has none


def test_the_same_values_again_change_nothing_and_other_values_replace(api: Api) -> None:
    run_id = live(api, [stay("s-1", 2), stay("s-2", 2)])
    assert put(api, run_id, ep("s-1"), ep("s-2")).status_code == 204
    first = stored(api)
    api.wall.now = api.wall.now.replace(hour=13)
    assert put(api, run_id, ep("s-1")).status_code == 204
    assert stored(api) == first  # received_at too
    assert put(api, run_id, ep("s-1", "tachycardic", 140, 170)).status_code == 204
    after = stored(api)
    assert [(r["patient_id"], r["kind"], r["start_minute"], r["full_minute"]) for r in after] == [
        ("s-1", "tachycardic", 140, 170),
        ("s-2", "respiratory", 135, 150),
    ]
    assert after[0]["received_at"] == api.wall.now and after[1] == first[1]


def test_200_entries_are_accepted(api: Api) -> None:
    ids = [f"s-{i}" for i in range(200)]
    run_id = live(api, [sim_patient(i) for i in ids])
    assert put(api, run_id, *(ep(i) for i in ids)).status_code == 204
    assert len(stored(api)) == 200


def test_a_start_up_to_15_minutes_past_the_newest_minute_is_accepted(api: Api) -> None:
    run_id = live(api, [stay("s-1", 2), stay("s-2", 2)])
    assert put(api, run_id, ep("s-1", start_minute=NOW_MINUTE + 16, full_minute=NOW_MINUTE + 30)).status_code == 422
    assert put(api, run_id, ep("s-2", start_minute=NOW_MINUTE + 15, full_minute=NOW_MINUTE + 30)).status_code == 204
    assert [r["patient_id"] for r in stored(api)] == ["s-2"]


def test_the_rows_go_with_the_run_and_with_the_patient(api: Api) -> None:
    run_id = live(api, [stay("s-1", 2), stay("s-2", 2)])
    assert put(api, run_id, ep("s-1"), ep("s-2")).status_code == 204
    with connect(api.url) as conn:
        conn.execute("DELETE FROM patient WHERE run_id = %s AND id = 's-1'", (run_id,))
    assert [r["patient_id"] for r in stored(api)] == ["s-2"]
    with connect(api.url) as conn:
        conn.execute("DELETE FROM run WHERE id = %s", (run_id,))
    assert stored(api) == []


# --- The table ---------------------------------------------------------------------------------


def test_the_table_refuses_what_the_route_refuses(api: Api) -> None:
    run_id = live(api, [stay("s-1", 2)])
    good = (run_id, "s-1", "respiratory", 5, 9)
    insert = "INSERT INTO sim_episode VALUES (%s, %s, %s, %s, %s, now())"
    for row in [(run_id, "s-1", "afib", 5, 9), (run_id, "s-1", "respiratory", -1, 9), (run_id, "s-1", "respiratory", 5, 4),
                (run_id, "s-9", "respiratory", 5, 9)]:  # fmt: skip
        with pytest.raises(Exception, match="sim_episode"), connect(api.url) as conn:
            conn.execute(insert, row)
    with connect(api.url) as conn:
        conn.execute(insert, good)
    with pytest.raises(Exception, match="sim_episode_pkey"), connect(api.url) as conn:
        conn.execute(insert, good)


def test_the_migration_is_in_the_list() -> None:
    assert "0012_sim_episode" in [p.stem for p in migrations()]


# --- The history -------------------------------------------------------------------------------


def alert_at(index: int) -> dict[str, Any]:
    """A stay of 20 bins whose one alert bin is this one: its run's first bin ends at minute(index)."""
    return sick_bins(stay("s-1", 20), {index: SICK})


def test_no_stored_episode_means_no_episode_and_no_label(api: Api) -> None:
    live(api, [alert_at(8)])
    got = history(api)
    assert got["simulated_episode"] is None
    assert [r["in_episode"] for r in got["runs"]] == [None] and got["runs"][0]["state"] == "alert"


@pytest.mark.parametrize(
    ("index", "label"),
    [(7, "outside"), (8, "inside"), (9, "inside"), (17, "inside"), (18, "outside"), (19, "outside")],
)
def test_an_alert_is_inside_from_the_start_to_120_minutes_after_the_episode_is_full(api: Api, index: int, label: str) -> None:
    run_id = live(api, [alert_at(index)])
    assert minute(8) == 135 and minute(17) == 270  # start_minute 135, full_minute 150: inside to minute 270
    assert put(api, run_id, ep(start_minute=135, full_minute=150)).status_code == 204
    got = history(api)
    assert got["simulated_episode"] == {
        "kind": "respiratory", "start": served(api, 135), "full": served(api, 150), "source": "simulation",
    }  # fmt: skip
    (run,) = got["runs"]
    assert (run["state"], run["in_episode"]) == ("alert", label)


def test_a_run_that_began_before_the_episode_is_labelled_by_its_first_bin_only(api: Api) -> None:
    body = sick_bins(stay("s-1", 20), {6: SICK, 7: SICK, 8: SICK, 9: SICK})  # one run, first bin ends at 105
    run_id = live(api, [body])
    assert put(api, run_id, ep(start_minute=135, full_minute=150)).status_code == 204
    (run,) = history(api)["runs"]
    assert (run["bins"], run["in_episode"]) == (4, "outside")


def test_an_episode_with_no_alert_is_shown_with_no_run_at_all(api: Api) -> None:
    run_id = live(api)
    assert put(api, run_id, ep()).status_code == 204
    got = history(api)
    assert got["runs"] == [] and got["simulated_episode"]["kind"] == "respiratory"


def test_a_run_that_is_not_an_alert_is_never_labelled(api: Api) -> None:
    body = stay("s-1", 20)
    body["bins"] = [b for b in body["bins"] if not (b["vital"] == "heart_rate" and b["minute"] == minute(9))]
    run_id = live(api, [body])
    assert put(api, run_id, ep()).status_code == 204
    (run,) = history(api)["runs"]
    assert (run["state"], run["in_episode"]) == ("no_reading", None)


def test_an_episode_that_has_not_started_on_the_run_clock_is_not_shown(api: Api) -> None:
    run_id = live(api, [alert_at(8)])
    assert put(api, run_id, ep(start_minute=NOW_MINUTE + 15, full_minute=NOW_MINUTE + 40)).status_code == 204
    assert len(stored(api)) == 1  # stored inside the allowance
    got = history(api)
    assert got["simulated_episode"] is None and [r["in_episode"] for r in got["runs"]] == [None]
    later = batch(minute=NOW_MINUTE + 15, patients=[], observations=[], bins=[], events=[])
    assert api.post(f"/sim/runs/{run_id}/batch", later).status_code == 204
    assert history(api)["simulated_episode"]["start"] == served(api, NOW_MINUTE + 15)  # shown once the clock reaches it


def test_another_patients_episode_never_shows(api: Api) -> None:
    run_id = live(api, [stay("s-1", 20), stay("s-2", 20)])
    assert put(api, run_id, ep("s-2")).status_code == 204
    assert history(api, "s-1")["simulated_episode"] is None
    assert history(api, "s-2")["simulated_episode"] is not None


def test_an_older_runs_episode_for_the_same_patient_id_never_shows(api: Api) -> None:
    old = live(api)
    assert put(api, old, ep()).status_code == 204
    live(api)
    assert len(stored(api)) == 1
    assert history(api)["simulated_episode"] is None


def test_a_caller_who_may_not_see_the_patient_gets_the_same_404_and_no_truth(api: Api) -> None:
    run_id = live(api)
    assert put(api, run_id, ep()).status_code == 204
    response = get(api, "s-1", "rn-reyes")
    assert (response.status_code, response.json()) == (404, {"detail": "Not found"})
    assert "respiratory" not in response.text and "episode" not in response.text


def test_the_read_shows_no_episode_of_a_run_that_is_not_marked_synthetic(api: Api) -> None:
    """The post refuses such a run. The read does not trust that: it is a second gate."""
    run_id = live(api)
    assert put(api, run_id, ep()).status_code == 204
    assert history(api)["simulated_episode"] is not None
    with connect(api.url) as conn:
        conn.execute("UPDATE run SET source = 'fixture' WHERE id = %s", (run_id,))
    assert history(api)["simulated_episode"] is None
    assert [r["in_episode"] for r in history(api)["runs"]] == [None] * len(history(api)["runs"])


def test_an_admin_reads_the_episode_as_the_patient_page_does(api: Api) -> None:
    run_id = live(api)
    add_admin(api)
    assert put(api, run_id, ep()).status_code == 204
    assert get(api, "s-1", ADMIN).json()["simulated_episode"]["source"] == "simulation"


# --- The truth is read nowhere else ------------------------------------------------------------

WORDS = ("episode", "respiratory", "tachycardic", "hypotensive")


def test_no_other_answer_changes_by_a_byte_when_an_episode_is_stored(api: Api) -> None:
    run_id = live(api, [alert_at(8), stay("s-2", 20)])
    add_admin(api)
    paths = ["/wards/W4/rows", "/wards/W4/stays", "/wards/W4/overview", "/wards/W4/live", "/wards/W4/lab/runs", "/patients",
             "/patients/s-1", "/patients/s-2", "/patients/s-1/baseline", "/patients/s-2/baseline", "/patients/s-1/questions", "/acknowledgements"]  # fmt: skip

    def answers() -> bytes:
        out = []
        for who in ("rn-delacruz", "md-santos", "rn-manalo", ADMIN):
            for path in paths:
                r = api.client.get(path, headers=api.as_(who))
                body = r.content
                if path.endswith("/overview"):  # its activity counts the reads of this very test
                    body = json.dumps({k: v for k, v in r.json().items() if k not in ("activity", "clinicians")}).encode()
                out.append(f"{who} {path} {r.status_code} ".encode() + body)
        return b"\n".join(out)

    def context(who: str) -> str:  # one open question for each asker
        sent = api.client.post("/patients/s-1/questions", headers=api.as_(who), json={"question": "What changed?"})
        assert sent.status_code == 201, sent.text
        row = api.sql("SELECT context, context_sha256 FROM lab_run WHERE kind = 'patient_question' ORDER BY id DESC LIMIT 1")[0]
        return json.dumps(row, sort_keys=True)

    before_context = context("rn-delacruz")
    before = answers()
    assert put(api, run_id, ep("s-1", "hypotensive", 100, 160), ep("s-2", "hypotensive", 100, 160)).status_code == 204
    assert answers() == before
    after_context = context("md-santos")
    assert after_context == before_context
    for word in WORDS:
        assert word in before.decode() or word not in answers().decode()
        assert word in before_context or word not in after_context
    assert len(stored(api)) == 2

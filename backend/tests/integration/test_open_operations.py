"""Synthetic operational figures on the anonymous unit view."""

from dataclasses import replace

from fastapi.testclient import TestClient
from test_open_view import watch  # noqa: F401
from test_store import Api, api, batch, new_run, sim_patient  # noqa: F401

from biosentry.api.app import create_app


def _run(api: Api) -> int:
    units = [
        {"id": "W4", "name": "Ward W4", "floor": 4, "kind": "ranked", "capacity": 20},
        {"id": "ER", "name": "Emergency Room", "floor": 0, "kind": "shown", "capacity": 12},
        {"id": "ICU", "name": "Intensive Care", "floor": 2, "kind": "unranked", "capacity": 6},
    ]
    patients = [
        {**sim_patient(f"w-{i}"), "unit": "W4", "bed": f"4{chr(65 + i)}"}
        for i in range(7)
    ] + [{**sim_patient("e-1", "cn-er", None), "unit": "ER", "bed": "E1"}]
    response = api.post("/sim/runs", {**new_run(patients=patients), "units": units})
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def _batch(api: Api, run_id: int, minute: int, **over: object) -> None:
    body = batch(minute=minute, patients=[], observations=[], bins=[], events=[], **over)
    response = api.post(f"/sim/runs/{run_id}/batch", body)
    assert response.status_code == 204, response.text


def _open(api: Api, unit: str = "W4") -> dict:
    response = api.client.get(f"/wards/{unit}/open-view")
    assert response.status_code == 200, response.text
    return response.json()


def test_capacity_snapshots_er_and_suppressed_outcomes(watch: Api) -> None:
    run_id = _run(watch)
    before = _open(watch)
    assert (before["capacity"], before["nurses_on_duty"], before["awaiting_icu"], before["outliers"]) == (20, None, None, None)
    assert before["er"] == {"capacity": 12, "beds_in_use": 1, "awaiting_icu": None}
    assert (before["transfers_24h"], before["deaths_24h"]) == (0, 0)
    assert watch.client.get("/open-view").json()["units"] == [
        {**u, "score_note": None} for u in (
            {"id": "W4", "name": "Ward W4", "floor": 4, "kind": "ranked", "capacity": 20},
            {"id": "ER", "name": "Emergency Room", "floor": 0, "kind": "shown", "capacity": 12},
            {"id": "ICU", "name": "Intensive Care", "floor": 2, "kind": "unranked", "capacity": 6},
        )
    ]

    operations = [
        {"unit": "W4", "nurses_on_duty": 4, "awaiting_icu": 2, "outliers": 1},
        {"unit": "ER", "nurses_on_duty": 3, "awaiting_icu": 1, "outliers": 0},
    ]
    departures = [
        {"patient_id": f"w-{i}", "minute": 30, "outcome": "transferred", "to_unit": "ICU"}
        for i in range(5)
    ] + [{"patient_id": f"w-{i}", "minute": 30, "outcome": "died"} for i in (5, 6)]
    _batch(watch, run_id, 30, operations=operations, departures=departures)
    ward = _open(watch)
    assert (ward["nurses_on_duty"], ward["awaiting_icu"], ward["outliers"]) == (4, 2, 1)
    assert (ward["transfers_24h"], ward["deaths_24h"]) == (5, None)
    assert ward["er"] == {"capacity": 12, "beds_in_use": 1, "awaiting_icu": 1}
    er = _open(watch, "ER")
    assert (er["capacity"], er["nurses_on_duty"], er["transfers_24h"], er["deaths_24h"]) == (12, 3, 0, 0)
    assert watch.client.get("/wards/W4/open-view", headers=watch.as_("md-santos")).json() == ward

    _batch(watch, run_id, 1501)
    assert (_open(watch)["transfers_24h"], _open(watch)["deaths_24h"]) == (0, 0)
    assert _open(watch)["nurses_on_duty"] == 4  # a batch without a snapshot preserves the last one
    _batch(watch, run_id, 1499, operations=[{"unit": "W4", "nurses_on_duty": 0, "awaiting_icu": 0, "outliers": 0}])
    assert _open(watch)["nurses_on_duty"] == 4  # a late batch cannot replace a newer snapshot


def test_operations_reject_bad_units_duplicates_and_negative_counts(watch: Api) -> None:
    run_id = _run(watch)
    good = {"unit": "W4", "nurses_on_duty": 0, "awaiting_icu": 0, "outliers": 0}
    for operations, status in (
        ([good, good], 422),
        ([{**good, "unit": "NOPE"}], 422),
        ([{**good, "outliers": -1}], 422),
    ):
        response = watch.post(
            f"/sim/runs/{run_id}/batch",
            batch(minute=10, patients=[], observations=[], bins=[], events=[], operations=operations),
        )
        assert response.status_code == status
    assert _open(watch)["nurses_on_duty"] is None


def test_capacity_rejects_non_positive_and_non_numeric_values(watch: Api) -> None:
    for capacity in (0, -1, True, "12"):
        response = watch.post(
            "/sim/runs",
            {**new_run(), "units": [{"id": "W4", "name": "Ward W4", "floor": 4, "kind": "ranked", "capacity": capacity}]},
        )
        assert response.status_code == 422


def test_operational_figures_use_the_existing_open_view_cache(api: Api) -> None:
    run_id = _run(api)
    client = TestClient(create_app(replace(api.settings, open_ward_view=True, view_cache_seconds=60), clock=api.wall))
    first = client.get("/wards/W4/open-view").json()
    assert first["nurses_on_duty"] is None
    _batch(api, run_id, 10, operations=[{"unit": "W4", "nurses_on_duty": 5, "awaiting_icu": 0, "outliers": 0}])
    assert client.get("/wards/W4/open-view").json()["nurses_on_duty"] == 5
    assert client.get("/wards/W4/open-view").json()["nurses_on_duty"] == 5

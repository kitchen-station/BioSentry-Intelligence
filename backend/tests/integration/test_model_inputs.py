"""The stored inputs of the models, from a database: the temperature stop-check, the two batched queries, the service
on a posted run, and the reference file replayed through the ingest routes."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from golden import VITALS, charted_events, load  # isort: skip
from test_store import NOW, ORIGIN, Api, api, batch, obs, sim_patient, start  # noqa: F401 (api: fixture)

from biosentry.domain.computed import Computed, NotComputed
from biosentry.repositories.postgres import PostgresStore, connect
from biosentry.services import model_signals as ms
from biosentry.services.model_signals import ward_model_signals
from biosentry.services.models import load_models

MODELS = load_models(Path(__file__).resolve().parents[3] / "models")
RUN0 = datetime.fromisoformat(ORIGIN)
BASE = {"heart_rate": 70.0, "respiratory_rate": 16.0, "spo2": 97.0, "temperature": 36.8}
CHARTED = [
    obs("p", "blood_pressure", -10, "nurse", num=120, num2=80),
    obs("p", "consciousness", -10, "nurse", txt="A"),
    obs("p", "supplemental_o2", -10, "nurse", num=0),
    obs("p", "spo2_scale", -10, "nurse", num=1),
]


def stay(pid: str, n: int, first: int = 15, **over: Any) -> dict[str, Any]:
    """A quiet patient present from the start of the run with n stored bins of all four vitals, the way the
    simulator posts them (the bin's `minute` is the END of its window), a live reading and charted inputs."""
    last = first + 15 * (n - 1)
    device = [obs(pid, v, last, num=BASE[v]) for v in VITALS]
    charted = [{**o, "patient_id": pid} for o in CHARTED]
    bins = [
        {"patient_id": pid, "vital": v, "minute": first + 15 * i, "value": BASE[v] + (0.5 if i % 2 else 0.0)}
        for v in VITALS
        for i in range(n)
    ]
    return {**sim_patient(pid), "observations": [*device, *charted], "bins": bins, **over}


def read(api: Api, *pids: str) -> tuple[PostgresStore, dict[str, Any]]:
    conn = connect(api.url)
    store = PostgresStore(conn, api.wall)
    return store, {pid: store.read(pid) for pid in pids}


class Counting:
    """The connection, with every statement counted."""

    def __init__(self, conn: Any) -> None:
        self.conn, self.sql = conn, []

    def execute(self, query: str, *a: Any, **k: Any) -> Any:
        self.sql.append(query)
        return self.conn.execute(query, *a, **k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.conn, name)


def test_a_run_posted_as_the_simulator_builds_it_stores_temperature_for_the_whole_stay(api: Api) -> None:
    """STOP-CHECK: an admission, then batches with bins for all four device vitals for 30 hours of sim time."""
    run_id = start(api)
    first = {**stay("s-2", 0), "minute": 0, "observations": [], "bins": []}
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=0, admissions=[first], observations=[], bins=[], events=[])).status_code == 204
    for hour in range(6, 31, 6):
        minute = hour * 60
        bins = [
            {"patient_id": "s-2", "vital": v, "minute": m, "value": BASE[v] + (m % 3)}
            for v in VITALS
            for m in range(minute - 360 + 15, minute + 1, 15)
        ]
        body = batch(minute=minute, observations=[obs("s-2", "heart_rate", minute, num=70)], bins=bins, events=[],
                     patients=[{"id": "s-2", "trend": "stable", "signal_quality": 90, "last_sample_minute": minute}])
        assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    store, states = read(api, "s-2")
    temperature = store.temperature_bins(["s-2"])["s-2"]
    heart = states["s-2"].vital_history.heart_rate
    assert len(heart) == len(temperature) == 120  # 30 hours of bins, not the seed's 12
    assert temperature[0].time == heart[0].time and temperature[-1].time == heart[-1].time
    assert "temperature" not in states["s-2"].vital_history.model_dump()  # not in any response


def test_the_two_batched_queries_return_what_the_rule_needs_in_one_statement_each(api: Api) -> None:
    pids = ["q-1", "q-2", "q-3"]
    patients = [stay(p, 30 + i, first=15 * (i + 1)) for i, p in enumerate(pids)]
    start(api, patients=patients)
    run_id = api.sql("SELECT max(id) AS i FROM run")[0]["i"]
    later = batch(minute=60, patients=[], bins=[], events=[], observations=[
        obs("q-1", "blood_pressure", 30, "nurse", num=100, num2=60),
        obs("q-2", "blood_pressure", 90, "nurse", num=100, num2=60),
        obs("q-1", "heart_rate", 40, "nurse", num=80),
    ])
    assert api.post(f"/sim/runs/{run_id}/batch", later).status_code == 204
    store, _ = read(api)
    counting = Counting(store.conn)
    store.conn = counting
    temperature = store.temperature_bins(pids + ["nobody"])
    assert len(counting.sql) == 1 and set(temperature) == set(pids)
    for i, p in enumerate(pids):
        times = [t.time for t in temperature[p]]
        assert times == sorted(times) and len(times) == 30 + i
    assert store.temperature_bins([]) == {}  # one statement even for none is still a statement, never a loop
    windows = [(p, RUN0 + timedelta(minutes=lo) + store.shift, RUN0 + timedelta(minutes=hi) + store.shift)
               for p, lo, hi in [("q-1", 35, 60), ("q-2", -5, 20), ("q-3", 200, 300)]]
    counting.sql.clear()
    got = store.observations_between(windows)
    assert len(counting.sql) == 1
    everything = api.sql("SELECT patient_id, code, observed_at, id FROM observation WHERE run_id = %s", run_id)
    codes = {"blood_pressure", "consciousness", "supplemental_o2", "spo2_scale", *VITALS}
    for p, lo, hi in windows:
        lo, hi = lo - store.shift, hi - store.shift
        mine = [r for r in everything if r["patient_id"] == p and r["code"] in codes]
        want = {r["id"] for r in mine if lo <= r["observed_at"] <= hi}
        for code in codes - set(VITALS):
            before = [r for r in mine if r["code"] == code and r["observed_at"] < lo]
            if before:
                want.add(max(before, key=lambda r: (r["observed_at"], r["id"]))["id"])
        assert {o.id for o in got.get(p, [])} == want, p
        assert [(o.observed_at, o.id) for o in got.get(p, [])] == sorted((o.observed_at, o.id) for o in got.get(p, []))
    assert any(o.source == "nurse" and o.code == "heart_rate" for o in got["q-1"])
    counting.sql.clear()
    assert store.observations_between([]) == {} and counting.sql == []


def test_the_service_on_a_posted_run(api: Api) -> None:
    no_bp = stay("q-2", 30)
    no_bp["observations"] = [o for o in no_bp["observations"] if o["code"] != "blood_pressure"] + [
        obs("q-2", "blood_pressure", -10, "nurse", absent_reason="not_recorded")]
    patients = [stay("q-1", 30), no_bp, stay("q-3", 10), stay("q-4", 30), stay("q-5", 0)]
    start(api, patients=patients)
    api.wall.now = NOW + timedelta(minutes=460)  # after the last device reading: the typed value then stands
    posted = api.client.post("/patients/q-4/vitals", headers=api.as_("rn-delacruz"), json={"heart_rate": 100})
    assert posted.status_code == 200
    api.wall.now = NOW
    pids = ["q-1", "q-2", "q-3", "q-4", "q-5"]
    store, states = read(api, *pids)
    counting = Counting(store.conn)
    store.conn = counting
    got = ward_model_signals(store, states, MODELS)
    assert len(counting.sql) == 2  # two statements for the whole set
    ok = got["q-1"]
    assert isinstance(ok.probability, Computed) and isinstance(ok.check, Computed) and ok.policy == Computed(False, ok.policy.provenance, ok.policy.window)  # type: ignore[union-attr]
    assert ok.provenance["tier_c/student_vitals"].sim_run_id == store.run_id
    assert ok.provenance["tier_c/student_vitals"].bin_end == ms.bin_window(ok.probability.provenance.observed_at)[1]
    assert (got["q-2"].probability, got["q-2"].policy) == (NotComputed(ms.NOT_RANKED),) * 2
    assert (got["q-3"].probability, got["q-3"].check, got["q-3"].policy) == (NotComputed(ms.FORMING),) * 3
    assert (got["q-4"].probability, got["q-4"].check, got["q-4"].policy) == (NotComputed(ms.NOT_COUNTED),) * 3
    assert got["q-5"].probability == NotComputed(ms.NO_BINS)  # live and ranked, but nothing stored yet
    counting.sql.clear()
    assert set(ward_model_signals(store, {k: states[k] for k in ("q-2", "q-4")}, MODELS)) == {"q-2", "q-4"}
    assert counting.sql == []  # NOT RANKED and not counted patients cost no model query
    only = ward_model_signals(store, {"q-3": states["q-3"]}, MODELS)
    assert only["q-3"].policy == NotComputed(ms.FORMING)


def test_the_reference_stay_replayed_through_the_ingest_routes_gives_the_files_last_bin(api: Api) -> None:
    """The document's "one trial replayed through the backend path" (INTEGRATION.md section 8.4 (a))."""
    doc = load()
    synthetic = next(s for s in doc["stays"] if s["id"] == "synthetic-long")
    last = synthetic["bins"][-1]
    assert last["student"] is not None
    pid, end = "g-1", last["minute"]
    observations = [
        obs(pid, code, minute, "nurse", **(value or {"absent_reason": "not_recorded"}))
        for code, minute, value in charted_events(synthetic)
    ]
    bins = []
    for b in synthetic["bins"]:
        if b["row"] is None:
            continue
        for v in VITALS:
            bins.append({"patient_id": pid, "vital": v, "minute": b["minute"], "value": b["row"][v]})
            if b["row"][v] is not None:
                observations.append(obs(pid, v, b["minute"], num=b["row"][v]))
    run_id = start(api)
    admission = {**sim_patient(pid), "minute": synthetic["admit_minute"], "observations": observations, "bins": bins}
    api.wall.now = datetime.fromisoformat(ORIGIN) + timedelta(minutes=end, seconds=5)
    body = batch(minute=end, admissions=[admission], observations=[], bins=[], events=[], patients=[])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    store, states = read(api, pid)
    got = ward_model_signals(store, states, MODELS)[pid]
    ref, pol = last["student"]["all"], last["policy"]["all"]
    assert isinstance(got.probability, Computed) and isinstance(got.check, Computed)
    assert abs(got.probability.value - ref["p"]) <= 1e-12 and got.check.value == ref["check"]
    assert isinstance(got.policy, Computed) and got.policy.value == pol["alerting"]

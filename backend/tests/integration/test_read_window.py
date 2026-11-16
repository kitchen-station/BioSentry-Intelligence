"""What a read of one patient takes from the database. A card, an open view and the row of a unit that is
not ranked need the newest values and the last 12 bins; only the models (a ranked unit) and the patient page
need the whole stored history. The newest row of each kind is found with one index probe each."""

from test_open_bed_history import bins
from test_overview import add_admin
from test_store import Api, api  # noqa: F401 (api: fixture)
from test_units import PATIENTS, get, hospital, stay

from biosentry.repositories.postgres import TREND_BINS, PostgresStore, connect

LONG = list(range(-600, 0, 15))  # 40 bins of each vital, 10 hours


def long_stay(pid: str, unit: str, rn: str, md: str | None, bed: str) -> dict:
    first = stay(pid, unit, rn, md, bed)
    extra = [b for v in ("heart_rate", "respiratory_rate", "spo2", "temperature") for b in bins(pid, LONG, v)]
    return {**first, "bins": extra}


def store(api: Api) -> PostgresStore:
    return PostgresStore(connect(api.url), api.wall)


def test_a_read_without_history_is_the_same_state_with_the_last_12_bins_and_no_history(api: Api) -> None:
    hospital(api, [long_stay("w-1", "W4", "rn-delacruz", "md-santos", "4A"), *PATIENTS[1:]])
    s = store(api)
    full, short = s.read("w-1"), s.read("w-1", False)
    assert full and short and len(full.vital_history.heart_rate) == len(LONG)
    assert full.model_dump(exclude={"vital_history"}) == short.model_dump(exclude={"vital_history"})
    assert len(short.vital_trends.heart_rate) == TREND_BINS == len(full.vital_trends.heart_rate)
    assert [short.vital_history.heart_rate, short.vital_history.respiratory_rate, short.vital_history.spo2] == [[], [], []]


def test_the_page_of_a_patient_of_a_unit_that_is_not_ranked_still_has_the_whole_history(api: Api) -> None:
    long_er = long_stay("e-1", "ER", "cn-er", None, "E1")
    hospital(api, [PATIENTS[0], long_er, *PATIENTS[2:]])
    admin = add_admin(api)
    page = api.client.get("/patients/e-1", headers=admin).json()
    assert len(page["vital_history"]["heart_rate"]) == len(LONG)  # the page asked for it
    [row] = api.client.get("/wards/ER/rows", headers=admin).json()["abstained"]
    assert len(row["vital_trends"]["heart_rate"]) == TREND_BINS  # the list row has the last 12 only
    assert "vital_history" not in row


def test_a_read_decides_its_history_by_the_unit_when_asked_to(api: Api) -> None:
    hospital(api, [long_stay("w-1", "W4", "rn-delacruz", "md-santos", "4A"), long_stay("e-1", "ER", "cn-er", None, "E1"), *PATIENTS[2:]])
    s = store(api)
    ranked, shown = s.read("w-1", None), s.read("e-1", None)
    assert ranked and shown and len(ranked.vital_history.heart_rate) == len(LONG) and shown.vital_history.heart_rate == []
    forced = s.read("e-1", True)
    assert forced and len(forced.vital_history.heart_rate) == len(LONG)


def test_the_newest_row_of_each_kind_has_its_own_index(api: Api) -> None:
    hospital(api)
    names = {r["indexname"] for r in api.sql("SELECT indexname FROM pg_indexes WHERE tablename = 'observation'")}
    assert "observation_latest_kind" in names
    get(api, "md-santos", "/wards/W4/rows")  # and the read that uses it answers

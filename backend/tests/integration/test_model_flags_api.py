"""The two research models on the live ward: a flag adds a reason line and changes nothing else (T1 c), a flag
needs the check to say alert (T2 b, c, d), and what the response says of each model. Synthetic, not validated."""

import hashlib
import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_model_inputs import stay  # isort: skip
from test_store import DATA, NOW, Api, api, obs, start  # noqa: F401 (api: fixture)

from biosentry.api.app import create_app
from biosentry.core.audit import AuditLog
from biosentry.domain.arbiter import POLICY_FLAG, PREDICTION_FLAG
from biosentry.domain.computed import NotComputed
from biosentry.repositories.postgres import PostgresStore, connect
from biosentry.schemas import WardResponse
from biosentry.services import model_signals as ms
from biosentry.services import ward as ward_module
from biosentry.services.models import CALIBRATION, CALIBRATION_SIBLING, MANIFEST, POLICY, STUDENT, load_models
from biosentry.services.ward import WardService

REAL = Path(__file__).resolve().parents[3] / "models"
WIRED = (MANIFEST, STUDENT, CALIBRATION, CALIBRATION_SIBLING, POLICY)
RN = "rn-delacruz"


def world(api: Api) -> None:
    """Five sim patients: two that compute, one whose baseline is forming, one with no BP, one ranked high."""
    voice = stay("w-2", 30)
    voice["observations"] = [
        {**o, "txt": "V"} if o["code"] == "consciousness" else o for o in voice["observations"]
    ]
    no_bp = stay("w-4", 30)
    no_bp["observations"] = [o for o in no_bp["observations"] if o["code"] != "blood_pressure"] + [
        obs("w-4", "blood_pressure", -10, "nurse", absent_reason="not_recorded")
    ]
    start(api, patients=[stay("w-1", 30), voice, stay("w-3", 10), no_bp, stay("w-5", 31)])


def folder(tmp_path: Path, change: Callable[[Path], None] | None = None) -> Path:
    root = tmp_path / "models"
    for rel in WIRED:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REAL / rel, root / rel)
    if change is not None:
        change(root)
    return root


def other_model(root: Path) -> None:
    """The calibration, bound to another student; the manifest and the checksum sibling agree with its new bytes."""
    path = root / CALIBRATION
    body = json.loads(path.read_text("utf-8"))
    body["onnx_sha256"] = "0" * 64
    data = (json.dumps(body, indent=1, sort_keys=True) + "\n").encode()
    path.write_bytes(data)
    (root / CALIBRATION_SIBLING).write_text(hashlib.sha256(data).hexdigest() + "\n", "ascii")
    doc = json.loads((root / MANIFEST).read_text("utf-8"))
    for record in doc["models"]:
        if record["path"] == f"models/{CALIBRATION}":
            record["bytes"], record["sha256"] = len(data), hashlib.sha256(data).hexdigest()
    (root / MANIFEST).write_text(json.dumps(doc), "utf-8")


def flip_a_byte(root: Path) -> None:
    data = bytearray((root / CALIBRATION).read_bytes())
    data[-3] ^= 1
    (root / CALIBRATION).write_bytes(bytes(data))  # the manifest keeps the old checksum


def client_for(api: Api, models_dir: Path) -> TestClient:
    return TestClient(create_app(replace(api.settings, models_dir=models_dir), clock=api.wall))


def read(api: Api, client: TestClient, path: str = "/wards/W4/rows") -> Any:
    api.wall.now += timedelta(seconds=61)  # outside the audit minute; the bins stay the same
    response = client.get(path, headers=api.as_(RN))
    assert response.status_code == 200
    return response.json()


def force(monkeypatch: pytest.MonkeyPatch, p: float | None = None, alert: bool | None = None) -> None:
    if p is not None:
        monkeypatch.setattr(ms, "forward", lambda student, x: p)
    if alert is not None:
        monkeypatch.setattr(ms, "_alerting", lambda prep, rows, pol, now, touched: pol if isinstance(pol, NotComputed) else alert)


def texts(row: dict[str, Any]) -> list[str]:
    return [r["text"] for r in row["escalation_reasons"]]


def bare(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in ("models", "escalation_reasons")}


# --- T1 (c): a model can move a row (must be impossible) ------------------------------------


def test_t1c_a_flag_adds_reason_lines_and_changes_nothing_else(api: Api, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    world(api)
    loaded, refused = client_for(api, REAL), client_for(api, tmp_path / "no-models")
    on = read(api, loaded)
    off = read(api, refused)
    force(monkeypatch, p=1.0, alert=True)
    flagged = read(api, loaded)
    assert len(on["ranked"]) == 4 and [a["id"] for a in on["abstained"]] == ["w-4"]
    for other in (off, flagged):
        assert [r["id"] for r in other["ranked"]] == [r["id"] for r in on["ranked"]]
        assert [r["rank"] for r in other["ranked"]] == [r["rank"] for r in on["ranked"]]
        assert other["abstained"] == on["abstained"]  # an abstained row is the same, and has no `models` (below)
        assert [bare(r) for r in other["ranked"]] == [bare(r) for r in on["ranked"]]
    assert [texts(r) for r in off["ranked"]] == [texts(r) for r in on["ranked"]]
    for row, then in zip(flagged["ranked"], on["ranked"], strict=True):
        computes = row["models"]["prediction"]["state"] != "not_computed"
        assert texts(row) == texts(then) + ([PREDICTION_FLAG, POLICY_FLAG] if computes else [])
    assert sum(r["models"]["prediction"]["state"] == "flag" for r in flagged["ranked"]) == 3  # w-3 is forming
    # The service itself, loaded versus every file refused: the same rows apart from the model fields.
    store = PostgresStore(connect(api.url), api.wall)
    audit = AuditLog(store.conn, api.wall, store.role_of)
    ids = store.on_ward()
    with_models = WardService(store, audit, load_models(REAL)).compute(ids)
    none = WardService(store, audit, load_models(tmp_path / "no-models")).compute(ids)
    dump = lambda rows: [r.model_dump(exclude={"models", "escalation_reasons"}) for r in rows]  # noqa: E731
    assert [dump(x) for x in with_models] == [dump(x) for x in none]
    assert all(r.models.prediction.state == "flag" for r in with_models[0] if r.id != "w-3")
    assert all(r.models.prediction.state == "not_computed" for r in none[0])


def test_the_response_shape_and_what_each_state_carries(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    world(api)
    client = client_for(api, REAL)
    quiet = read(api, client)
    force(monkeypatch, p=1.0, alert=True)
    flagged = read(api, client)
    files = {r["path"]: r for r in json.loads((REAL / MANIFEST).read_text("utf-8"))["models"]}
    sha = lambda rel: hashlib.sha256((REAL / rel).read_bytes()).hexdigest()  # noqa: E731
    assert all(set(r["models"]) == {"prediction", "alert_policy"} for r in quiet["ranked"])
    assert all("models" not in a for a in quiet["abstained"])
    for row in quiet["ranked"]:
        for signal in row["models"].values():
            assert set(signal) == {"state", "reason", "bin_start", "bin_end", "provenance"}
            assert (signal["reason"] is not None) is (signal["state"] == "not_computed")
            assert (signal["bin_end"] is not None) is (signal["state"] != "not_computed")
            assert (signal["bin_start"] is not None) is (signal["bin_end"] is not None)
            assert bool(signal["provenance"]) is (signal["state"] != "not_computed")
        assert "probability" not in json.dumps(row["models"])
    by_id = {r["id"]: r["models"] for r in quiet["ranked"]}
    assert by_id["w-1"]["prediction"]["state"] == "no_flag" and by_id["w-1"]["alert_policy"]["state"] == "no_flag"
    assert by_id["w-3"]["prediction"] == {"state": "not_computed", "reason": ms.FORMING, "bin_start": None, "bin_end": None, "provenance": []}
    one = {r["id"]: r["models"] for r in flagged["ranked"]}["w-1"]
    pred, policy = one["prediction"], one["alert_policy"]
    assert [p["model_id"] for p in pred["provenance"]] == [ms.STUDENT_ID, ms.CHECK_ID]
    assert [p["model_id"] for p in policy["provenance"]] == [ms.POLICY_ID]
    for signal, wanted in ((pred, (STUDENT, CALIBRATION)), (policy, (POLICY,))):
        for prov, rel in zip(signal["provenance"], wanted, strict=True):
            assert (prov["variant"], prov["file_sha256"], prov["run_id"]) == ("all", sha(rel), files[f"models/{rel}"]["run_id"])
            assert (prov["source"], prov["synthetic"], prov["observed_at"]) == ("model", True, signal["bin_end"])
    assert [p["tier"] for p in pred["provenance"]] == ["C", "C"] and policy["provenance"][0]["tier"] == "B"
    assert pred["bin_start"] < pred["bin_end"] == policy["bin_end"]
    WardResponse.model_validate(flagged)  # the contract accepts it, and the ward row and the page agree below
    detail = client.get("/patients/w-1", headers=api.as_(RN)).json()
    assert detail["models"] == one and texts(detail) == texts({r["id"]: r for r in flagged["ranked"]}["w-1"])


# --- T2: a flag without the check saying alert (must be impossible) -------------------------


@pytest.mark.parametrize("change", [lambda r: (r / CALIBRATION).unlink(), flip_a_byte, other_model],
                         ids=["missing", "refused", "other-model"])
def test_t2b_without_a_usable_calibration_a_student_at_one_flags_nobody(
    api: Api, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: Callable[[Path], None]
) -> None:
    world(api)
    force(monkeypatch, p=1.0)  # the student says 1.0 for everyone
    got = read(api, client_for(api, folder(tmp_path, change)))
    for row in got["ranked"]:
        assert PREDICTION_FLAG not in texts(row)
        prediction = row["models"]["prediction"]
        assert prediction["state"] == "not_computed"
        assert prediction["reason"] in ("calibration not loaded", ms.FORMING)
    assert [r["models"]["prediction"]["reason"] for r in got["ranked"]].count("calibration not loaded") == 3


def test_t2c_a_score_inside_the_not_sure_range_is_not_sure_and_adds_no_reason(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    world(api)
    force(monkeypatch, p=0.01)  # between the threshold (0.006) and the calibrated q (about 0.0199)
    got = read(api, client_for(api, REAL))
    unsure = [r for r in got["ranked"] if r["models"]["prediction"]["state"] == "not_sure"]
    assert len(unsure) == 3
    for row in unsure:
        assert PREDICTION_FLAG not in texts(row) and row["models"]["prediction"]["reason"] is None


def test_t2d_a_row_is_flagged_if_and_only_if_its_reason_is_there(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    world(api)
    client = client_for(api, REAL)
    seen = set()
    for p, alert in ((1.0, True), (0.01, False), (0.0, True), (1.0, False)):
        force(monkeypatch, p=p, alert=alert)
        for row in read(api, client)["ranked"]:
            m = row["models"]
            assert (m["prediction"]["state"] == "flag") is (PREDICTION_FLAG in texts(row))
            assert (m["alert_policy"]["state"] == "flag") is (POLICY_FLAG in texts(row))
            seen |= {m["prediction"]["state"], m["alert_policy"]["state"]}
    assert seen == {"flag", "no_flag", "not_sure", "not_computed"}


# --- The API with no files, one load, and the fixture ward ----------------------------------


def test_the_api_starts_and_serves_with_an_empty_models_folder(
    api: Api, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    world(api)
    loads: list[Path] = []
    real = load_models
    monkeypatch.setattr("biosentry.api.app.load_models", lambda d: (loads.append(d), real(d))[1])
    empty = tmp_path / "empty"
    empty.mkdir()
    with caplog.at_level(logging.WARNING, logger="biosentry.services.models"):
        client = client_for(api, empty)
    assert loads == [empty]
    assert len([r for r in caplog.records if r.name == "biosentry.services.models"]) == 3  # one per file, at start
    got = read(api, client)
    assert {r["models"]["prediction"]["state"] for r in got["ranked"]} == {"not_computed"}
    assert {r["models"]["alert_policy"]["state"] for r in got["ranked"]} == {"not_computed"}
    read(api, client)
    read(api, client, "/patients/w-1")
    assert loads == [empty]  # no file is read per request
    assert [r["id"] for r in got["ranked"]] == [r["id"] for r in read(api, client_for(api, REAL))["ranked"]]


def test_each_fixed_reason_is_logged_once_per_process_without_patient_data(
    api: Api, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    world(api)
    monkeypatch.setattr(ward_module, "_LOGGED", set())
    client = client_for(api, REAL)
    with caplog.at_level(logging.WARNING, logger="biosentry.services.ward"):
        read(api, client)
        first = [r.getMessage() for r in caplog.records]
        read(api, client)
        read(api, client, "/patients/w-1")
    assert [r.getMessage() for r in caplog.records] == first
    assert sorted(first) == sorted(f"model result not computed: {r}" for r in {ms.FORMING, ms.NOT_RANKED, ms.LATEST, ms.NOT_COUNTED} & {m.split(": ", 1)[1] for m in first})
    assert first and not any(p in m for m in first for p in ("w-1", "w-2", "w-3", "w-4", "w-5"))


def test_the_seeded_ward_serves_what_the_fixture_file_says(api: Api) -> None:
    """The public demo's data holds the value the server computes for the seeded ward: not computed, with the reason."""
    seeded = {r["id"]: r for r in DATA["ranked"]}
    shown = 0
    for who, entry in DATA["clinicians"].items():
        if entry["role"] not in ("rn", "md", "charge_rn"):
            continue
        for row in api.ward(who)["ranked"]:
            assert row["models"] == seeded[row["id"]]["models"], row["id"]
            assert row["models"]["prediction"]["state"] == "not_computed"
            shown += 1
    assert shown >= len(seeded)
    assert api.client.get("/patients/p-001", headers=api.as_("rn-manalo")).json()["models"] == seeded["p-001"]["models"]


def counted_statements(api: Api, monkeypatch: pytest.MonkeyPatch, client: TestClient, without: bool) -> int:
    seen = [0]
    real = psycopg.Connection.execute

    def counting(self: Any, *args: Any, **kw: Any) -> Any:
        seen[0] += 1
        return real(self, *args, **kw)

    api.as_(RN)
    with monkeypatch.context() as m:
        m.setattr(psycopg.Connection, "execute", counting)
        if without:  # the same read with the models' own work taken out
            m.setattr(ward_module, "ward_model_signals", lambda store, states, models: {k: ms._none("off") for k in states})
        read(api, client)
    return seen[0]


def test_a_ward_read_costs_two_statements_more_when_a_patient_is_eligible_and_none_when_not(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    world(api)
    client = client_for(api, REAL)
    assert counted_statements(api, monkeypatch, client, False) == counted_statements(api, monkeypatch, client, True) + 2


def test_a_ward_read_costs_nothing_more_when_no_patient_is_eligible(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = [obs(f"n-{i}", "blood_pressure", -10, "nurse", absent_reason="not_recorded") for i in (1, 2)]
    start(api, patients=[{**stay(f"n-{i}", 30), "observations": [gone[i - 1]]} for i in (1, 2)])  # both NOT RANKED
    client = client_for(api, REAL)
    assert counted_statements(api, monkeypatch, client, False) == counted_statements(api, monkeypatch, client, True)

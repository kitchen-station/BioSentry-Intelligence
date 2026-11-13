"""The model results for one patient: every NotComputed reason, the as-of rule, the impossible earlier value,
the provenance and the pieces under them. A small world: 30 quiet bins, charted inputs, models from the real files."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from samples import absent, measurement, present, unscored  # isort: skip

from biosentry.domain.computed import Computed, NotComputed
from biosentry.domain.scoring.tier_a import News2Abstained, News2Score
from biosentry.domain.scoring.tier_c import features
from biosentry.domain.scoring.tier_c.confidence import Check, check_calibrated
from biosentry.repositories.postgres import StoredObservation
from biosentry.schemas import UnscoredPatient, VitalHistory
from biosentry.services import model_signals as ms
from biosentry.services.as_of import news2_as_of
from biosentry.services.model_signals import ModelSignals, model_signals
from biosentry.services.models import load_models

MODELS = load_models(Path(__file__).resolve().parents[3] / "models")
T0 = datetime(2026, 1, 1, 6, tzinfo=UTC)
NOW = datetime(2026, 1, 1, 20, tzinfo=UTC)
VITALS = ("heart_rate", "respiratory_rate", "spo2", "temperature")
BASE = {"heart_rate": 70.0, "respiratory_rate": 16.0, "spo2": 97.0, "temperature": 36.8}


def slot(i: int) -> datetime:
    return T0 + timedelta(minutes=15 * i)


def pts(n: int = 30, **over: Any) -> dict[str, list[VitalHistory]]:
    """Quiet bins with a small wobble; over[vital] = {index: value} replaces a bin (None is a gap)."""
    out = {}
    for v in VITALS:
        vals = [BASE[v] + (0.5 if i % 2 else 0.0) for i in range(n)]
        for i, x in over.get(v, {}).items():
            vals[i] = x
        out[v] = [VitalHistory.model_construct(time=slot(i), value=x) for i, x in enumerate(vals)]
    return out


def charted(**over: Any) -> list[StoredObservation]:
    base = {"blood_pressure": {"num": 120.0, "num2": 80.0}, "consciousness": {"txt": "A"},
            "supplemental_o2": {"num": 0.0}, "spo2_scale": {"num": 1.0}}
    rows, n = [], 0
    for code, value in {**base, **over}.items():
        for v in value if isinstance(value, list) else [value]:
            n += 1
            at = v.get("at", T0 - timedelta(hours=1))
            body = {k: x for k, x in v.items() if k != "at"}
            rows.append(StoredObservation("p", code, "nurse", at, n, "ok", body.get("num"), body.get("num2"),
                                          body.get("txt"), body.get("absent_reason")))
    return rows


def state(**over: Any) -> UnscoredPatient:
    s = unscored()
    s["vitals"].update(
        heart_rate=measurement(present(70)), respiratory_rate=measurement(present(16)),
        spo2=measurement(present(97)), temperature=measurement(present(36.8)),
        blood_pressure=measurement(present({"systolic": 120, "diastolic": 80})),
        supplemental_o2=measurement(present(False)), spo2_scale=measurement(present(1)))
    if over.pop("no_o2", False):
        s["vitals"]["supplemental_o2"] = measurement(absent())
    if over.pop("nurse_hr", False):
        s["vitals"]["heart_rate"]["provenance"]["source"] = "nurse"
    s["last_update_s"] = 4
    s["admitted_at"] = T0 - timedelta(hours=1)
    h = pts(**over.pop("points", {}))
    s["vital_history"] = {v: [{"time": p.time, "value": p.value} for p in h[v]] for v in VITALS[:3]}
    s.update(over)
    return UnscoredPatient.model_validate(s), h["temperature"]


def run(rows: list[StoredObservation] | None = None, models=MODELS, **over: Any) -> ModelSignals:  # type: ignore[no-untyped-def]
    st, temperature = state(**over)
    return model_signals(st, temperature, charted() if rows is None else rows, models, run_id=7, computed_at=NOW)


def reasons(got: ModelSignals) -> tuple[object, ...]:
    return tuple(r.reason if isinstance(r, NotComputed) else "ok" for r in (got.probability, got.check, got.policy))


def test_a_quiet_patient_with_a_formed_baseline_computes_all_three_with_provenance() -> None:
    got = run()
    assert isinstance(got.probability, Computed) and isinstance(got.check, Computed)
    assert isinstance(got.policy, Computed) and got.policy.value is False
    assert got.check.value in set(Check)
    prov = got.provenance
    assert set(prov) == {"tier_c/student_vitals", "tier_c/confidence_check", "tier_b/reward_policy"}
    s = prov["tier_c/student_vitals"]
    assert (s.variant, s.sim_run_id, s.bin_end, s.bin_start) == ("all", 7, slot(29), slot(29) - timedelta(minutes=15))
    assert (s.path, s.sha256, s.file_run_id) == tuple(getattr(MODELS.student_file, f) for f in ("path", "sha256", "run_id"))
    assert prov["tier_c/confidence_check"].path.endswith("calibration.json")
    assert prov["tier_b/reward_policy"].file_run_id == MODELS.policy_file.run_id  # type: ignore[union-attr]
    assert got.probability.provenance.observed_at == slot(29) and got.probability.provenance.model_id == "tier_c/student_vitals"
    assert got.probability.window.end_s - got.probability.window.start_s == 900


def test_not_ranked_now_is_not_ranked() -> None:
    got = run(no_o2=True)
    assert reasons(got) == (ms.NOT_RANKED,) * 3 and got.provenance == {}


def test_a_vital_not_counted_is_not_computed() -> None:
    assert reasons(run(last_update_s=3000)) == (ms.NOT_COUNTED,) * 3
    assert reasons(run(last_update_s=None)) == (ms.NOT_COUNTED,) * 3
    assert reasons(run(nurse_hr=True)) == (ms.NOT_COUNTED,) * 3


def test_no_stored_bins_and_a_stay_past_the_cap() -> None:
    assert ms.prepare_series({v: [] for v in VITALS}, None) == NotComputed(ms.NO_BINS)
    assert not isinstance(ms.prepare_series(pts(672), None), NotComputed)
    assert ms.prepare_series(pts(673), None) == NotComputed(ms.PAST_CAP)
    wide = pts(600)
    wide["temperature"] = [VitalHistory.model_construct(time=slot(i), value=36.8) for i in range(100, 701)]
    assert ms.prepare_series(wide, None) == NotComputed(ms.PAST_CAP)  # the union of the vitals is past it
    off = pts(30)
    off["temperature"] = [VitalHistory.model_construct(time=p.time + timedelta(minutes=5), value=p.value)
                          for p in off["temperature"]]
    assert ms.prepare_series(off, None) == NotComputed(ms.NOT_ALIGNED)


def test_the_current_bin_not_scored_on_its_stored_inputs_is_not_ranked() -> None:
    assert reasons(run(rows=charted(blood_pressure={"at": slot(40)}))) == (ms.NOT_RANKED,) * 3
    assert reasons(run(rows=charted(consciousness={"absent_reason": "x"}))) == (ms.NOT_RANKED,) * 3


def test_a_baseline_still_forming_in_any_of_the_four_vitals_is_not_computed() -> None:
    n23 = pts(23)
    assert reasons(model_signals_from(n23)) == (ms.FORMING,) * 3
    short = pts(30)
    short["temperature"] = short["temperature"][7:]  # 23 valid temperature bins, the others 30
    assert reasons(model_signals_from(short)) == (ms.FORMING,) * 3


def model_signals_from(points: dict[str, list[VitalHistory]], rows=None, models=MODELS) -> ModelSignals:  # type: ignore[no-untyped-def]
    prep = ms.prepare_series(points, T0 - timedelta(hours=1))
    assert isinstance(prep, ms.Prepared)
    return ms.signals(prep, charted() if rows is None else rows, models, run_id=7, synthetic=True, computed_at=NOW)


def test_the_policy_needs_every_earlier_bin_of_its_window_rebuilt() -> None:
    # a cuff pressure first charted after the end of bin t-4 (index 25), before the current bin
    late = charted(blood_pressure={"num": 120.0, "num2": 80.0, "at": slot(26)})
    got = model_signals_from(pts(), late)
    assert reasons(got) == ("ok", "ok", ms.EARLIER)
    # a nurse typed a device vital 3 hours before the current bin: those bins are not rebuilt
    nurse = charted() + [StoredObservation("p", "heart_rate", "nurse", slot(25), 99, "ok", 70.0, None, None, None)]
    assert reasons(model_signals_from(pts(), nurse)) == ("ok", "ok", ms.EARLIER)
    # the same typed value 13 hours before bin t-4 has expired and does not matter
    old = charted() + [StoredObservation("p", "heart_rate", "nurse", slot(29) - timedelta(hours=17), 99, "ok", 70.0, None, None, None)]
    assert reasons(model_signals_from(pts(), old))[2] == "ok"


def test_the_as_of_rule() -> None:
    dev = dict(BASE)
    end = slot(29)

    def total(rows: list[StoredObservation]) -> int | None:
        rb = news2_as_of(dev, rows, end, earlier=False)
        assert rb is not None
        return rb.result.total if isinstance(rb.result, News2Score) else None

    assert total(charted()) == 0
    assert total(charted(blood_pressure=[{"num": 120.0, "num2": 80.0}, {"num": 85.0, "num2": 50.0, "at": slot(10)}])) == 3  # the newest
    assert total(charted(blood_pressure=[{"num": 85.0, "num2": 50.0, "at": end + timedelta(minutes=1)}, {"num": 120.0, "num2": 80.0}])) == 0  # one after the end is not used
    assert total(charted(blood_pressure=[{"num": 85.0, "num2": 50.0, "at": end}])) == 3  # at the end counts
    assert total(charted(blood_pressure=[{"num": 120.0, "num2": 80.0}, {"absent_reason": "x", "at": slot(3)}])) is None  # absent is newest
    assert total(charted(spo2_scale={"num": 3.0})) is None
    assert total(charted(blood_pressure={"at": T0 - timedelta(days=30), "num": 120.0, "num2": 80.0})) == 0  # no age limit
    rows = [r for r in charted() if r.code != "blood_pressure"]
    assert news2_as_of(dev, rows, end, earlier=True) is None  # an earlier bin with no row at all: not stored
    rb = news2_as_of(dev, rows, end, earlier=False)
    assert rb is not None and isinstance(rb.result, News2Abstained)
    bad = replace(charted()[0], quality="missing")
    assert news2_as_of(dev, [bad, *charted()[1:]], end, earlier=False).result.__class__ is News2Abstained  # type: ignore[union-attr]
    assert news2_as_of({**dev, "spo2": None}, charted(), end, earlier=False).result.__class__ is News2Abstained  # type: ignore[union-attr]


def test_an_impossible_earlier_value_is_a_gap_and_the_narrow_rule_applies() -> None:
    def bad(i: int) -> ModelSignals:
        return model_signals_from(pts(30, heart_rate={i: 5000.0}))

    assert reasons(bad(25)) == (ms.IMPOSSIBLE, ms.IMPOSSIBLE, "ok")  # t-4: a lag bin; the policy sees a NOT RANKED bin
    assert bad(25).policy.value is False  # type: ignore[union-attr]
    assert reasons(bad(13)) == (ms.IMPOSSIBLE, ms.IMPOSSIBLE, ms.IMPOSSIBLE_BASELINE)  # t-16, inside the baseline window
    assert reasons(bad(5)) == (ms.IMPOSSIBLE, ms.IMPOSSIBLE, ms.IMPOSSIBLE_BASELINE)  # inside the baseline window
    assert reasons(bad(27)) == ("ok", "ok", "ok")  # an earlier bin that is neither: a gap, the policy run ends
    assert reasons(bad(29)) == (ms.NOT_RANKED,) * 3  # the current bin
    nan = pts(30)
    nan["spo2"][27] = VitalHistory.model_construct(time=slot(27), value=0.0)  # zero SpO2 is not a reading
    assert reasons(model_signals_from(nan)) == ("ok", "ok", "ok")


def test_a_model_that_did_not_load_gives_its_own_reason_and_nothing_else_changes() -> None:
    no_student = replace(MODELS, student=NotComputed("student not loaded"), student_file=None)
    assert reasons(model_signals_from(pts(), models=no_student)) == ("student not loaded", "student not loaded", "ok")
    no_cal = replace(MODELS, calibration=NotComputed("calibration not loaded"), calibration_file=None)
    assert reasons(model_signals_from(pts(), models=no_cal)) == ("ok", "calibration not loaded", "ok")
    no_pol = replace(MODELS, policy=NotComputed("policy not loaded"), policy_file=None)
    assert reasons(model_signals_from(pts(), models=no_pol)) == ("ok", "ok", "policy not loaded")
    window = ms.observation_window(ms.prepare_series(pts(), None), no_pol)  # type: ignore[arg-type]
    assert window == (slot(29) - timedelta(hours=12), slot(29))
    assert ms.observation_window(ms.prepare_series(pts(), None), MODELS)[0] == slot(25) - timedelta(hours=12)  # type: ignore[arg-type]


def test_a_feature_that_cannot_be_made_is_not_computed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> float:
        raise OverflowError

    monkeypatch.setattr(ms, "student_features", boom)
    assert reasons(model_signals_from(pts())) == (ms.FEATURES, ms.FEATURES, "ok")
    with pytest.raises(OverflowError):
        features.student_features({v: [1e39] * 30 for v in VITALS}, 29, news2_total=0, on_oxygen=False, consciousness_alert=True)


def test_features_take_missing_lags_and_not_alert_as_the_research_does() -> None:
    series = {v: [BASE[v]] * 3 for v in VITALS}
    row = features.student_features(series, 2, news2_total=5, on_oxygen=True, consciousness_alert=False)
    assert len(row) == 35 and row[2:4] == (0.0, 1.0)  # forming: z 0, flag 1
    assert row[16:19] == (5.0, 1.0, 1.0) and row[19:23] == (0.0, 1.0, 0.0, 1.0)  # no lag bin: 0 and flag 1
    long = {v: [BASE[v]] * 6 for v in VITALS} | {"heart_rate": [70.0, 71.0, 70.0, 71.0, 72.0, 75.5]}
    row = features.student_features(long, 5, news2_total=0, on_oxygen=False, consciousness_alert=True)
    assert row[19:21] == (4.5, 0.0)  # bin 5 minus bin 1
    long["heart_rate"][1] = None  # type: ignore[call-overload]
    row = features.student_features(long, 5, news2_total=0, on_oxygen=False, consciousness_alert=True)
    assert row[19:21] == (0.0, 1.0)  # a gap lag: 0 and the flag
    assert features.baseline_touched([1.0] * 3, [False, True, False]) and not features.baseline_touched([1.0] * 3, [False] * 3)


def test_the_check_takes_its_numbers_from_the_loaded_calibration() -> None:
    cal = MODELS.calibration
    assert not isinstance(cal, NotComputed) and cal.q is not None
    assert check_calibrated(cal, cal.t - 1e-9) is Check.QUIET
    assert check_calibrated(cal, cal.t) is Check.NOT_SURE
    assert check_calibrated(cal, (cal.t + cal.q) / 2) is Check.NOT_SURE
    assert check_calibrated(cal, cal.q) is Check.NOT_SURE
    assert check_calibrated(cal, cal.q + 1e-9) is Check.ALERT
    assert check_calibrated(replace(cal, q=None), 0.9) is Check.NOT_SURE

"""Real-data slice on a TINY synthetic fixture shaped like the eICU tables. Nothing here is a real row."""

import csv
import gzip
import hashlib

import pytest
from biosentry.domain.scoring.tier_a import News2Score, score_news2

from research.realdata import evaluate as rd_eval
from research.realdata import news2
from research.realdata.loader import ORIGIN, check_test_split, load_cohort
from research.realdata.source import RealDataError, verify
from research.realdata.split import TEST_HOSPITALS, held_out_by_rule
from research.rescore import score_row
from research.trials import Row

PATIENT = ["patientunitstayid", "uniquepid", "hospitalid", "age", "unitdischargeoffset", "unitdischargestatus"]
HEADERS = {
    "patient": PATIENT,
    "vitalPeriodic": ["patientunitstayid", "observationoffset", "temperature", "sao2", "heartrate", "respiration",
                      "systemicsystolic"],
    "vitalAperiodic": ["patientunitstayid", "observationoffset", "noninvasivesystolic"],
    "nurseCharting": ["patientunitstayid", "nursingchartentryoffset", "nursingchartcelltypevalname", "nursingchartvalue"],
    "respiratoryCharting": ["patientunitstayid", "respchartentryoffset", "respchartvaluelabel", "respchartvalue"],
    "infusiondrug": ["patientunitstayid", "infusionoffset", "drugname"],
    "carePlanGeneral": ["patientunitstayid", "cplitemoffset", "cplitemvalue"],
}
# stay id: (uniquepid, hospital, age, leave minute, status)
STAYS = {
    1: ("a", 60, "65", 2000, "Alive"),  # the bin-by-bin stay
    2: ("b", 60, "> 89", 1500, "Expired"),  # death event
    3: ("c", 60, "70", 3000, "Alive"),  # vasopressor at 500
    4: ("d", 60, "70", 3000, "Alive"),  # vasopressor at 359: left out
    5: ("e", 60, "17", 3000, "Alive"),  # not adult
    6: ("f", 60, "70", 600, "Alive"),  # under 12 hours
    7: ("g", 61, "70", 3000, "Alive"),  # a hospital that is not asked for
    8: ("a", 60, "65", 3000, "Alive"),  # second stay of patient a
    9: ("h", 60, "70", 2000, "Expired"),  # comfort care charted early: left out
    10: ("i", 60, "70", 2000, "Expired"),  # comfort care charted within 12 h of death: kept
    11: ("j", 60, "70", 3000, "Alive"),  # vasopressor at exactly 360: kept
    12: ("k", 60, "70", 3000, "Alive"),  # the control stay
}


def write(folder, name, rows):
    with gzip.open(folder / f"{name}.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADERS[name])
        w.writerows(rows)


def make(folder):
    folder.mkdir(exist_ok=True)
    patient = [[sid, p, h, age, leave, status] for sid, (p, h, age, leave, status) in STAYS.items()]
    vp = []
    for sid, (_, _, _, leave, _) in STAYS.items():
        if sid == 1:
            continue
        vp += [[sid, t, "", 97, 85, 16, ""] for t in range(0, leave, 5)]
    vp += [  # stay 1: three values in bin 15, then gaps, artefacts and a real zero
        [1, 5, "", 97, 80, 16, ""], [1, 10, "", 95, 100, 18, ""], [1, 14, "", 96, 90, 17, ""],
        [1, 40, "", "", 1500, "", ""], [1, 50, "", 101, 0, "", ""],
    ]
    nurse = [
        [1, 0, "Temperature (C)", "37.0"], [1, 0, "Temperature (F)", "98.6"], [1, 0, "GCS Total", "15"],
        [1, 0, "O2 Admin Device", "room air"], [1, 100, "GCS Total", "9"], [1, 100, "O2 Admin Device", "other"],
        [1, 300, "GCS Total", "Unable to score due to medication"],
        [1, 400, "Temperature (C)", "98.6"],  # Fahrenheit left in the Celsius column
    ]
    write(folder, "patient", patient)
    write(folder, "vitalPeriodic", vp)
    write(folder, "vitalAperiodic", [[1, 0, 120], [1, 200, 10]])
    write(folder, "nurseCharting", nurse)
    write(folder, "respiratoryCharting", [[1, 600, "O2 Device", "Nasal cannula"]])
    write(folder, "infusiondrug", [[3, 500, "Norepinephrine (mcg/min)"], [4, 359, "Vasopressin (units/min)"],
                                   [11, 360, "Phenylephrine (ml/hr)"], [11, 900, "Dopamine (mcg/kg/min)"],
                                   [12, 100, "Dobutamine (mcg/kg/min)"]])
    write(folder, "carePlanGeneral", [[9, 100, "Comfort measures only"], [10, 1500, "No CPR"]])
    lines = ["| file | sha256 | matched official |", "|---|---|---|"]
    for name in HEADERS:
        digest = hashlib.sha256((folder / f"{name}.csv.gz").read_bytes()).hexdigest()
        lines.append(f"| {name}.csv.gz | {digest} | yes |")
    (folder / "SOURCE.md").write_text("\n".join(lines) + "\n")
    return folder


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return make(tmp_path_factory.mktemp("eicu"))


@pytest.fixture(scope="module")
def cohort(data):
    return load_cohort(data, frozenset({60}))


def row_of(cohort, patient, m):
    (r,) = [r for r in cohort.trial.rows if r.patient == patient and r.minute == m + ORIGIN]
    return r


def test_bin_reduction_is_the_median_and_gaps_stay_gaps(cohort):
    r = row_of(cohort, "1", 15)  # values at 5, 10, 14 minutes
    assert (r.heart_rate, r.respiratory_rate, r.spo2) == (90, 17, 96)
    g = row_of(cohort, "1", 30)  # nothing in (15, 30]: missing, not carried and not interpolated
    assert (g.heart_rate, g.respiratory_rate, g.spo2) == (None, None, None)


def test_range_filters_use_the_backend_bounds(cohort):
    assert row_of(cohort, "1", 45).heart_rate is None  # 1500 > 1000
    r = row_of(cohort, "1", 60)
    assert r.heart_rate == 0 and r.spo2 is None  # heart rate 0 is a reading, SpO2 101 is not
    assert row_of(cohort, "1", 210).systolic is None  # 10 mmHg is outside 20 to 300
    assert cohort.counts["dropped out of range: heart_rate"] == 1


def test_impossible_temperature_is_dropped_and_counted(cohort):
    assert row_of(cohort, "1", 405).temperature is None  # 98.6 is outside 30-45 C; the 37.0 is 405 min old
    assert cohort.counts["dropped out of range: temperature"] == 1


def test_celsius_only_and_values_are_not_converted(cohort):
    assert row_of(cohort, "1", 15).temperature == 37.0  # the Fahrenheit entry is not read


def test_stale_values_become_missing_with_their_age_kept(cohort):
    assert row_of(cohort, "1", 240).temperature == 37.0 and row_of(cohort, "1", 255).temperature is None
    assert row_of(cohort, "1", 60).systolic == 120 and row_of(cohort, "1", 75).systolic is None
    assert row_of(cohort, "1", 60).bp_minute == 0 + ORIGIN


def test_consciousness_and_oxygen_mapping(cohort):
    assert row_of(cohort, "1", 15).consciousness == "A" and row_of(cohort, "1", 15).on_oxygen is False
    r = row_of(cohort, "1", 105)
    assert r.consciousness == "C"  # GCS 9: not A
    assert r.on_oxygen is False  # "other" is unclear: it does not overwrite the last known device
    assert row_of(cohort, "1", 315).consciousness == "C"  # text GCS is ignored, GCS 9 from minute 100 still fresh
    assert row_of(cohort, "1", 345).consciousness is None  # 245 minutes after the GCS 9: stale
    assert row_of(cohort, "1", 600).on_oxygen is True  # nasal cannula from the respiratory table


def test_oxygen_text_is_never_guessed():
    from research.realdata.loader import consciousness_from_gcs, oxygen_status
    assert oxygen_status("Room Air ") is False and oxygen_status("Venturi mask") is True
    assert oxygen_status("other") is None and oxygen_status("") is None and oxygen_status("xyz") is None
    assert consciousness_from_gcs("15") == "A" and consciousness_from_gcs("3") == "C"
    assert consciousness_from_gcs("2") is None and consciousness_from_gcs("Unable to score") is None


def test_the_cohort_rules(cohort):
    c = cohort.counts
    assert c["stays in these hospitals"] == 11  # stay 7 is in hospital 61
    assert c["not the first stay of the patient"] == 1  # stay 8
    assert c["not adult"] == 1 and c["unit stay under 12 hours"] == 1
    assert c["vasopressor before hour 6"] == 1  # 359 minutes
    assert c["comfort or no-CPR death"] == 1  # stay 9; stay 10 is within 12 h of death
    patients = {e.patient for e in cohort.trial.episodes}
    assert patients == {"1", "2", "3", "10", "11", "12"}


def test_event_time_and_the_six_hour_rule(cohort):
    ep = {e.patient: e for e in cohort.trial.episodes}
    assert ep["3"].kind == "vasopressor" and cohort.event_minute["3"] == 500 + ORIGIN
    assert ep["11"].kind == "vasopressor" and cohort.event_minute["11"] == 360 + ORIGIN  # first of two
    assert ep["2"].kind == "death" and cohort.event_minute["2"] == 1500 + ORIGIN
    assert ep["10"].kind == "death" and ep["1"].kind is None and ep["12"].kind is None  # dobutamine is not one
    last = max(r.minute for r in cohort.trial.rows if r.patient == "3")
    assert last < 500 + ORIGIN  # no bin at or after the event
    assert ep["2"].start == 1500 + ORIGIN - 720 and ep["2"].ramp_minutes == 720
    assert ep["3"].start == ORIGIN  # the 12-hour window would start before admission: shortened
    assert cohort.counts["events with fewer than 12 hours of look-back"] == 2  # stays 3 and 11


def test_split_by_hospital():
    assert held_out_by_rule({10, 20, 30, 40, 50, 60, 70, 80}) == {40, 80}
    assert len(TEST_HOSPITALS) == 46 and all(isinstance(h, int) for h in TEST_HOSPITALS)
    assert 60 in TEST_HOSPITALS and 61 not in TEST_HOSPITALS


def test_other_hospitals_are_never_loaded(data):
    assert {e.patient for e in load_cohort(data, frozenset({61})).trial.episodes} == {"7"}


def test_the_split_rule_is_checked_against_the_data(data):
    with pytest.raises(RealDataError, match="does not match the rule"):
        check_test_split(data)  # the fixture has 2 hospitals, not the real 186


def test_partial_news2_never_defaults(cohort):
    base = dict(patient="x", minute=30, spo2_scale=1, ranked=False, news2=None, bp_minute=None)
    only_hr = Row(heart_rate=135, respiratory_rate=None, spo2=None, temperature=None, systolic=None,
                  consciousness=None, on_oxygen=None, **base)
    assert news2.components(only_hr) == {"heart_rate": 3}  # nothing else appears, not even as 0
    assert news2.partial_total(only_hr) == 3 and news2.partial_chart_trigger(only_hr)
    assert not isinstance(score_row(only_hr), News2Score)  # the backend scorer abstains
    nothing = Row(heart_rate=None, respiratory_rate=None, spo2=None, temperature=None, systolic=None,
                  consciousness=None, on_oxygen=None, **base)
    assert news2.partial_total(nothing) is None and not news2.partial_chart_trigger(nothing)
    no_temp = Row(heart_rate=95, respiratory_rate=22, spo2=94, temperature=None, systolic=105,
                  consciousness="A", on_oxygen=False, **base)
    assert news2.partial_total(no_temp) == 1 + 2 + 1 + 1  # hr, rr, spo2, sbp; temperature unscored
    assert score_row(no_temp).__class__.__name__ == "News2Abstained"
    r15 = row_of(cohort, "1", 60)  # missing SpO2: NOT RANKED, no total
    assert r15.ranked is False and r15.news2 is None


def test_partial_total_equals_the_backend_total_when_inputs_are_full():
    for hr, rr, sp, t, sbp, c, o in [(135, 26, 91, 39.5, 85, "C", True), (45, 10, 93, 35.5, 105, "A", False),
                                     (70, 16, 97, 36.8, 120, "A", False), (112, 21, 95, 38.4, 225, "C", True)]:
        r = Row("x", 30, hr, rr, sp, t, sbp, None, c, o, 1, False, None)
        full = score_row(r)
        assert isinstance(full, News2Score) and news2.partial_total(r) == full.total


def test_checksum_refusal(tmp_path):
    folder = make(tmp_path / "d")
    load_cohort(folder, frozenset({60}))  # intact: loads
    (folder / "infusiondrug.csv.gz").write_bytes((folder / "infusiondrug.csv.gz").read_bytes() + b"x")
    with pytest.raises(RealDataError, match="infusiondrug.csv.gz: sha256 does not match"):
        load_cohort(folder, frozenset({60}))
    (folder / "SOURCE.md").unlink()
    with pytest.raises(RealDataError, match="SOURCE.md: missing"):
        verify(folder, ())
    other = make(tmp_path / "e")
    text = (other / "SOURCE.md").read_text().replace("| yes |", "| NO |", 1)
    (other / "SOURCE.md").write_text(text)
    with pytest.raises(RealDataError, match="not matching the official"):
        verify(other, ())
    third = make(tmp_path / "f")
    (third / "SOURCE.md").write_text("\n".join(
        line for line in (third / "SOURCE.md").read_text().splitlines() if "lab" not in line and "patient.csv" not in line) + "\n")
    with pytest.raises(RealDataError, match="patient.csv.gz is not listed"):
        verify(third, ("patient.csv.gz",))


def test_main_refuses_with_one_line_and_exit_2(tmp_path, capsys):
    folder = make(tmp_path / "d")
    (folder / "patient.csv.gz").write_bytes(b"")
    assert rd_eval.main([str(folder)]) == 2
    assert capsys.readouterr().out.count("\n") == 1


def test_complements_that_expose_a_small_count_are_suppressed():
    assert rd_eval.ratio(23, 24) == ">19/24" and rd_eval.ratio(19, 24) == "0.792 (19/24)"
    assert rd_eval.excess(23, 24, 5, 10) == "n/a" and rd_eval.interval((0.8, 1.0), 23, 24) == "n/a"
    assert rd_eval.share(10, 24, 2, 10) == "+0.02" or rd_eval.share(10, 24, 2, 10).startswith("+")
    assert rd_eval._g(0.63, (0.3, 0.9), 4) == "n/a (<5 event stays)"


def test_small_cells_are_suppressed():
    assert rd_eval.sc(4) == "<5" and rd_eval.sc(0) == "<5" and rd_eval.sc(5) == "5"
    assert rd_eval.ratio(3, 27) == "<5/27" and rd_eval.ratio(5, 27) == "0.185 (5/27)" and rd_eval.ratio(9, 4) == "<5"
    assert rd_eval.interval((0.1, 0.2), 4) == "n/a" and rd_eval.excess(4, 27, 100, 1000) == "n/a"


def test_the_printed_tables_hide_small_counts_and_are_deterministic(cohort):
    a, b = rd_eval.render(cohort), rd_eval.render(cohort)
    assert a == b and a[-1] == rd_eval.LAST_LINE
    text = "\n".join(a)
    assert "events: <5" in text and "vasopressor before hour 6: <5" in text
    assert rd_eval.PARTIAL_NAME in text and rd_eval.NOTE in text and "no conclusion can be drawn." in text
    assert "full NEWS2 inputs available:" in text and "Minimum detectable AUROC difference" in text
    assert "/tmp" not in text and "eicu" not in text.replace("eICU", "")  # no paths


def test_auc_and_average_precision_by_hand():
    pos = [0] * 21
    neg = [0] * 21
    pos[5], pos[1] = 1, 1  # two positives at 5 and 1
    neg[3], neg[1] = 1, 1  # two negatives at 3 and 1
    sums = pos + neg
    # pairs: 5>3, 5>1, 1<3, 1=1 (half) -> 2.5/4
    assert rd_eval.auc_from_counts(sums) == pytest.approx(0.625)
    # thresholds high to low: score 5 (tp1 fp0, P=1), score 3 (tp1 fp1), score 1 (tp2 fp2, P=.5)
    assert rd_eval.ap_from_counts(sums) == pytest.approx(0.5 * 1.0 + 0.5 * 0.5)
    assert rd_eval.auc_from_counts([0] * 42) is None


def test_minimum_detectable_difference_matches_the_plan_estimate():
    assert rd_eval.min_detectable_auc_difference(27, 373) == pytest.approx(0.155, abs=0.003)

"""The history's pure parts: the state of one bin, the fold of bins into runs, and the events. No database."""

from datetime import UTC, datetime, timedelta
from typing import Any

from biosentry.repositories.postgres import StoredObservation
from biosentry.services.history import MAX_EVENTS, MAX_RUNS, NO_READING, NOT_REBUILT, QUIET, BinState, classify, events, fold
from biosentry.services.model_signals import bin_window
from biosentry.services.scoring import BIN

T = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)  # the stored bin_at of bin 0: the END of its window
NOW = T + timedelta(hours=30)
HIGH = BinState("alert", 7, "high", ("Total 7 (≥7): high",))
MEDIUM = BinState("alert", 5, "medium", ("Total 5 (5-6): medium",))
NR = BinState("not_ranked", reasons=("Blood pressure missing",))
NB = BinState("not_rebuilt", reasons=(NOT_REBUILT,))
NONE = BinState("no_reading", reasons=(NO_READING,))
Q = QUIET


def runs(bins: list[BinState]) -> list[Any]:
    return fold(bins, T, NOW, True)[0]


def at(k: int) -> tuple[datetime, datetime]:
    return bin_window(T + k * BIN)


def test_a_quiet_stay_gives_no_run() -> None:
    assert fold([Q] * 5, T, NOW, True) == ([], False)
    assert fold([], T, NOW, True) == ([], False)


def test_one_alert_run_has_its_peak_its_reasons_and_the_window_of_its_bins() -> None:
    (run,) = runs([Q, MEDIUM, HIGH, MEDIUM, Q])
    assert (run.state, run.bins, run.ongoing) == ("alert", 3, False)
    assert (run.start, run.end) == (at(1)[0], at(3)[1])  # the START of the first bin, the END of the last
    assert (run.peak_score, run.peak_level, run.peak_at) == (7, "high", at(2)[0])
    assert run.reasons == ["Total 7 (≥7): high"]
    assert (run.provenance.source, run.provenance.tier, run.provenance.rule) == ("derived", "A", "news2_as_of_bins")
    assert (run.provenance.computed_at, run.provenance.synthetic) == (NOW, True)


def test_a_peak_tie_takes_the_earliest_bin() -> None:
    other = BinState("alert", 7, "high", ("later",))
    (run,) = runs([MEDIUM, HIGH, other, HIGH])
    assert run.peak_at == at(1)[0] and run.reasons == ["Total 7 (≥7): high"]


def test_one_quiet_bin_splits_an_alert_run_in_two() -> None:
    first, second = runs([HIGH, HIGH, Q, MEDIUM])
    assert (first.bins, first.ongoing, second.bins, second.ongoing) == (2, False, 1, True)
    assert second.peak_level == "medium" and second.start == at(3)[0]


def test_states_other_than_alert_are_runs_without_a_peak() -> None:
    got = runs([NONE, NONE, NR, NB, NB, NB, Q, NR])
    assert [(r.state, r.bins) for r in got] == [("no_reading", 2), ("not_ranked", 1), ("not_rebuilt", 3), ("not_ranked", 1)]
    assert all(r.peak_score is None and r.peak_level is None and r.peak_at is None for r in got)
    assert got[0].reasons == [NO_READING] and got[1].reasons == ["Blood pressure missing"] and got[2].reasons == [NOT_REBUILT]


def test_ongoing_is_true_only_for_a_run_that_reaches_the_latest_bin() -> None:
    assert [r.ongoing for r in runs([HIGH, Q, HIGH])] == [False, True]
    assert [r.ongoing for r in runs([HIGH, HIGH, Q])] == [False]


def test_only_the_newest_200_runs_are_kept_and_the_cut_is_said() -> None:
    bins = [HIGH, Q] * (MAX_RUNS + 1)
    got, cut = fold(bins, T, NOW, True)
    assert cut and len(got) == MAX_RUNS
    assert got[-1].start == at(2 * MAX_RUNS)[0] and got[0].start == at(2)[0]  # the first run is the one dropped
    assert fold(bins[:-2], T, NOW, True)[1] is False  # exactly 200 runs: nothing cut


# --- one bin ---------------------------------------------------------------------------------

END = T + timedelta(hours=1)
CHARTED = {"blood_pressure": 120, "consciousness": "A", "supplemental_o2": 0, "spo2_scale": 1}
QUIET_VALUES = {"heart_rate": 70.0, "respiratory_rate": 16.0, "spo2": 97.0, "temperature": 36.8}


def row(code: str, i: int, source: str = "nurse", **over: Any) -> StoredObservation:
    value = CHARTED.get(code)
    base = {"patient_id": "p", "code": code, "source": source, "observed_at": END - timedelta(hours=1), "id": i,
            "quality": "ok", "num": value if isinstance(value, int) else None, "num2": None,
            "txt": value if isinstance(value, str) else None, "absent_reason": None}
    return StoredObservation(**{**base, **over})


ROWS = [row(c, i) for i, c in enumerate(CHARTED)]


def test_a_bin_is_quiet_alert_not_ranked_not_rebuilt_or_without_a_reading() -> None:
    assert classify(QUIET_VALUES, ROWS, END, earlier=True) == QUIET
    sick = {**QUIET_VALUES, "respiratory_rate": 25.0, "spo2": 91.0, "heart_rate": 131.0}
    got = classify(sick, ROWS, END, earlier=True)
    assert (got.state, got.level) == ("alert", "high") and got.total is not None and got.total >= 7
    assert got.reasons[0].startswith("Total ") and got.reasons[-1].startswith("Single parameter at 3")
    single = classify({**QUIET_VALUES, "spo2": 91.0}, ROWS, END, earlier=True)
    assert (single.state, single.level, single.total) == ("alert", "single_3", 3)
    assert single.reasons == ("Single parameter at 3: SpO2",)
    assert classify({**QUIET_VALUES, "heart_rate": None}, ROWS, END, earlier=False) == BinState("no_reading", reasons=(NO_READING,))
    assert classify(QUIET_VALUES, [], END, earlier=True) == BinState("not_rebuilt", reasons=(NOT_REBUILT,))
    last = classify(QUIET_VALUES, [], END, earlier=False)  # the last bin is not "not rebuilt": it abstains
    assert last.state == "not_ranked" and "Systolic BP missing" in last.reasons[0]


def test_a_nurse_typed_device_value_in_the_last_12_hours_makes_an_earlier_bin_not_rebuilt() -> None:
    typed = row("heart_rate", 9, observed_at=END - timedelta(hours=12), num=80.0)
    assert classify(QUIET_VALUES, [*ROWS, typed], END, earlier=True).state == "not_rebuilt"
    assert classify(QUIET_VALUES, [*ROWS, typed], END, earlier=False).state == "quiet"
    old = row("heart_rate", 9, observed_at=END - timedelta(hours=12, seconds=1), num=80.0)
    assert classify(QUIET_VALUES, [*ROWS, old], END, earlier=True).state == "quiet"


# --- events ----------------------------------------------------------------------------------

START, STOP = T - BIN, T + timedelta(hours=6)


def stored(kind: str, minute: int, **over: Any) -> dict[str, Any]:
    source = "clinical_note" if kind == "add_note" else "clinician_action"
    return {"source": source, "kind": kind, "actor": "rn-1", "at": START + timedelta(minutes=minute), "score": None,
            "note_id": None, "role": None, **over}


def test_events_are_what_was_done_oldest_first_with_no_note_text() -> None:
    bp = row("blood_pressure", 20, observed_at=START + timedelta(minutes=50), num=118, num2=70)
    kept = [
        bp,
        row("blood_pressure", 21, source="device", observed_at=START + timedelta(minutes=51), num=118),
        row("blood_pressure", 22, observed_at=START + timedelta(minutes=52), num=None, absent_reason="not_recorded"),
        row("blood_pressure", 23, observed_at=START - timedelta(minutes=1), num=118),  # before the grid
        row("consciousness", 24, observed_at=START + timedelta(minutes=53), txt="A"),
    ]
    note = stored("add_note", 30, actor=None, note_id="n-004", role="RN")
    done, cut = events(
        START - timedelta(days=1),
        kept,
        [stored("acknowledge", 10, score=7), stored("record_vitals", 40), note, stored("add_note", 60, actor=None, note_id="n-5", role="Dr Zed")],
        START,
        STOP,
    )
    assert cut is False
    assert [(e.kind, e.source) for e in done] == [
        ("admitted", "patient"), ("acknowledged", "clinician_action"), ("note_added", "clinical_note"),
        ("vitals_recorded", "clinician_action"), ("bp_charted", "observation"), ("note_added", "clinical_note"),
    ]
    assert [e.at for e in done] == sorted(e.at for e in done)
    ack, note_event = done[1], done[2]
    assert (ack.by, ack.score, ack.role, ack.note_id) == ("rn-1", 7, None, None)
    assert (note_event.by, note_event.role, note_event.note_id) == (None, "rn", "n-004")
    assert done[-1].role is None  # a role the client typed that is not a role of the ward is not served
    assert "text" not in str(done) and "content" not in str(done)


def test_the_admission_is_kept_while_it_is_not_after_the_grid_and_the_newest_300_events_are_kept() -> None:
    assert events(None, [], [], START, STOP) == ([], False)
    assert events(STOP + BIN, [], [], START, STOP)[0] == []
    many = [stored("record_vitals", i) for i in range(MAX_EVENTS + 1)]
    done, cut = events(START - timedelta(days=1), [], many, START, STOP)
    assert cut and len(done) == MAX_EVENTS and done[-1].at == many[-1]["at"]
    assert done[0].at == many[1]["at"]  # the admission and the oldest one were dropped


def test_a_not_ranked_run_ends_when_its_reason_changes() -> None:
    other = BinState("not_ranked", reasons=("Level of consciousness missing",))
    first, second = runs([NR, NR, other, Q])
    assert (first.state, first.bins, first.reasons) == ("not_ranked", 2, ["Blood pressure missing"])
    assert (second.state, second.bins, second.reasons) == ("not_ranked", 1, ["Level of consciousness missing"])
    assert second.start == first.end
    (alert,) = runs([MEDIUM, HIGH])  # an alert run does not split on its reasons
    assert alert.bins == 2

import pytest

from helpers import rows, row
from research.alerts import POLICIES, REARM_BINS, fire_alerts

CHART, TOTAL5 = POLICIES


def seq(first: str, gap_bins: int, second: str, filler: str = "N0", as_rows: bool = False) -> dict:
    """`first` at minute 15, then gap_bins bins that are either missing or filler rows, then `second`."""
    states = {15: first}
    nxt = 30
    for _ in range(gap_bins):
        if as_rows:
            states[nxt] = filler
        nxt += 15
    states[nxt] = second
    return rows("p", states)


def test_policy_names_and_order():
    assert [p.name for p in POLICIES] == ["news2_chart_trigger", "news2_total_ge_5"]


def test_chart_trigger_is_any_single_three_or_total_5():
    for state, chart, total5 in [("N0", 0, 0), ("M3", 0, 0), ("S3", 1, 0), ("T5", 1, 1), ("N9", 1, 1), ("NR", 0, 0)]:
        assert CHART.alerting(row("p", 15, state)) == bool(chart), state
        assert TOTAL5.alerting(row("p", 15, state)) == bool(total5), state


def test_flip_fires_once_and_continuing_does_not_refire():
    r = rows("p", {15: "N0", 30: "T5", 45: "T5", 60: "N9", 75: "T5"})
    assert fire_alerts(CHART, r) == [30]


def test_first_bin_already_alerting_fires():
    assert fire_alerts(CHART, rows("p", {15: "T5"})) == [15]


def test_one_open_alert_flip_inside_the_open_alert_is_ignored():
    r = rows("p", {15: "T5", 30: "N0", 45: "T5", 60: "N0", 75: "T5"})
    assert fire_alerts(TOTAL5, r) == [15]


@pytest.mark.parametrize("quiet,expected", [(REARM_BINS - 1, 1), (REARM_BINS, 2)])
def test_rearm_needs_16_bins_not_alerting(quiet, expected):
    assert len(fire_alerts(CHART, seq("T5", quiet, "T5", as_rows=True))) == expected


@pytest.mark.parametrize("missing,expected", [(REARM_BINS - 1, 1), (REARM_BINS, 2)])
def test_a_gap_counts_as_not_alerting(missing, expected):
    assert len(fire_alerts(CHART, seq("T5", missing, "T5"))) == expected


@pytest.mark.parametrize("stretch,expected", [(REARM_BINS - 1, 1), (REARM_BINS, 2)])
def test_a_not_ranked_stretch_counts_as_not_alerting(stretch, expected):
    assert len(fire_alerts(TOTAL5, seq("T5", stretch, "T5", filler="NR", as_rows=True))) == expected


def test_not_ranked_row_never_alerts_and_alert_ends():
    r = rows("p", {15: "T5", 30: "NR", 45: "T5"})
    assert fire_alerts(CHART, r) == [15]  # NR was a quiet bin, but 1 < 16 so still the same alert


def test_gap_before_the_first_row_does_not_matter():
    assert fire_alerts(CHART, rows("p", {900: "T5"})) == [900]


def test_rearm_is_exactly_16_bins_pinned_by_literal_minutes():
    # on at 15; the next on at 255 follows 15 empty bins (30 to 240), at 270 follows 16 (30 to 255)
    assert fire_alerts(CHART, rows("p", {15: "T5", 255: "T5"})) == [15]
    assert fire_alerts(CHART, rows("p", {15: "T5", 270: "T5"})) == [15, 270]
    filler = {m: "N0" for m in range(30, 256, 15)}  # 16 quiet bins as rows instead of gaps
    assert fire_alerts(CHART, rows("p", {15: "T5", **filler, 270: "T5"})) == [15, 270]
    assert fire_alerts(CHART, rows("p", {15: "T5", **{m: "N0" for m in range(30, 241, 15)}, 255: "T5"})) == [15]

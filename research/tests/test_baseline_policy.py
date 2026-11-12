import pytest

from biosentry.domain.scoring.tier_b.baseline import VITALS, BaselineParams, baseline_finding
from helpers import STATES, row
from research.alerts import POLICIES, fire_alerts
from research.baseline_policy import NO_TEMP, baseline_policies, baseline_states, clear_cache
from research.trials import Row


def mk(minute: int, hr, rr=16, spo2=97, state: str = "N0") -> Row:
    _, _, _, news2, ranked = STATES[state]
    return Row("p", minute, hr, rr, spo2, 36.8, 120, None, "A", False, 1, ranked, news2)


def series(hrs: list, **kw) -> dict[int, Row]:
    """hrs[i] is the heart rate at minute 15 * (i + 1); None leaves the bin without a row."""
    return {15 * (i + 1): mk(15 * (i + 1), h, **kw) for i, h in enumerate(hrs) if h != "gap"}


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()


def test_needs_24_valid_bins():
    # p=1: the first high bin that is the 24th valid bin is the first that can alert
    states = baseline_states(series([70] * 22 + [90, 90, 90]), 2.0, 1)
    assert [m for m, s in states.items() if s][0] == 24 * 15
    assert not any(baseline_states(series([70] * 22 + [90]), 2.0, 1).values())
    states = baseline_states(series([70] * 24 + [90, 90, 90]), 2.0, 3)
    assert [m for m, s in states.items() if s] == [27 * 15]  # the third high bin, not before


def test_a_gap_is_not_filled_and_breaks_the_run():
    hrs = [70] * 24 + [90, 90, "gap", 90, 90, 90]
    assert [m for m, s in baseline_states(series(hrs), 2.0, 3).items() if s] == [30 * 15]


def test_missing_value_in_a_row_gives_no_finding():
    rows = series([70] * 24 + [90, 90, 90])
    rows[27 * 15] = mk(27 * 15, None)
    assert not any(baseline_states(rows, 2.0, 3).values())


def test_causal_a_later_bin_never_changes_an_earlier_state():
    base = [70] * 24 + [90, 90, 90, 70, 70, 95, 95, 95]
    full = baseline_states(series(base), 2.0, 3)
    for cut in range(len(base)):
        clear_cache()
        changed = base[: cut + 1] + [200 - h for h in base[cut + 1:]]
        got = baseline_states(series(changed), 2.0, 3)
        assert all(got[m] == full[m] for m in got if m <= 15 * (cut + 1))


def test_equals_baseline_finding_called_per_bin():
    hr = [70, 71, 69, 72, 70, 68, 70, 71, 73, 70, 69, 70] * 4 + [84, 85, 86, 70, 90, "gap", 91, 92, 93, 94]
    rr = [16] * 30 + [22] * 28
    spo2 = [97] * 40 + [None] * 3 + [92] * 15
    rows = {}
    for i, h in enumerate(hr):
        m = 15 * (i + 1)
        if h != "gap":
            rows[m] = mk(m, h, rr[i], spo2[i])
    bins = {v: [None if (r := rows.get(15 * (i + 1))) is None else getattr(r, v) for i in range(len(hr))]
            for v in VITALS}
    for k in (1.5, 2.0, 3.0):
        for p in (2, 3, 6):
            clear_cache()
            got = baseline_states(rows, k, p)
            params = BaselineParams(k=k, p=p)
            for m in rows:
                t = m // 15
                want = any(
                    isinstance(res := baseline_finding(v, bins[v][:t], params), tuple) and res[0].significant
                    for v in VITALS
                )
                assert got[m] == want, (k, p, m)


def test_or_policy_alerts_when_either_does_and_alone_ignores_news2():
    alone, either = baseline_policies(2.0, 3)
    chart_only = {15 * (i + 1): row("p", 15 * (i + 1), "S3" if i == 5 else "N0") for i in range(30)}
    assert fire_alerts(POLICIES[0], chart_only) == [90]
    assert fire_alerts(either, chart_only) == [90] and fire_alerts(alone, chart_only) == []
    clear_cache()
    base_only = series([70] * 24 + [90, 90, 90])  # NEWS2 total 0 throughout
    assert fire_alerts(POLICIES[0], base_only) == []
    assert fire_alerts(alone, base_only) == fire_alerts(either, base_only) == [27 * 15]
    clear_cache()
    quiet = series([70] * 30)
    assert fire_alerts(either, quiet) == fire_alerts(alone, quiet) == []


def test_no_temperature_policy_ignores_a_temperature_only_deviation():
    rows = {}
    for i in range(30):
        m = 15 * (i + 1)
        r = mk(m, 70)
        rows[m] = Row(r.patient, m, 70, 16, 97, 36.8 if i < 24 else 37.8, 120, None, "A", False, 1, True, 0)
    alone, _ = baseline_policies(2.0, 3)
    alone_nt, either_nt = baseline_policies(2.0, 3, "_no_temp", NO_TEMP)
    assert fire_alerts(alone, rows) != []
    assert fire_alerts(alone_nt, rows) == [] and fire_alerts(either_nt, rows) == []


def test_no_temp_set_is_the_other_three_vitals():
    assert NO_TEMP == ("heart_rate", "respiratory_rate", "spo2")

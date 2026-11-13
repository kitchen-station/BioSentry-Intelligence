import dataclasses
import json
import math
from pathlib import Path

import pytest

from research import confidence as c
from research.alerts import scan
from research.bootstrap import SplitMix64
from research.trials import TrialError


@pytest.mark.parametrize("n, alpha, j, k", [
    (9, "0.1", 1, 9), (99, "0.1", 10, 90), (99, "0.01", 1, 99), (100, "0.01", 1, 100), (199, "0.01", 2, 198),
    (9, "0.01", 0, 10),
    (99, "0.29", 29, 71),  # float 0.29 * 100 is 28.999999999999996: exact arithmetic must give 29
])
def test_j_and_k_by_hand(n, alpha, j, k):
    assert c.indices(n, alpha) == (j, k)


def test_q_is_the_kth_smallest_ties_included_and_inf_when_j_is_zero():
    assert c.quantile([5, 1, 3, 2, 4, 9, 8, 7, 6], "0.1") == (9, 1, 9, 9)  # q = the maximum
    assert c.quantile(list(range(1, 100)), "0.1") == (99, 10, 90, 90)
    assert c.quantile([0.5] * 99, "0.01")[3] == 0.5
    assert c.quantile([1, 2, 2, 2, 2, 2, 2, 2, 3, 3], "0.2") == (10, 2, 9, 3)  # 9th smallest of ties
    n, j, k, q = c.quantile(list(range(9)), "0.01")  # 9 stays cannot support any alert at 1%
    assert (j, q) == (0, None) and c.quantile([], "0.01")[3] is None


def test_check_truth_table_and_strict_greater_than():
    assert c.check(0.1, 0.2, 0.5) == "quiet"
    assert c.check(0.2, 0.2, 0.1) == "alert"  # q below t: the student rule decides
    assert c.check(0.3, 0.2, 0.5) == "not sure"
    assert c.check(0.5, 0.2, 0.5) == "not sure"  # a tie with q is conservative
    assert c.check(0.51, 0.2, 0.5) == "alert"
    assert c.check(1.0, 0.2, None) == "not sure"  # q = +inf: never supported
    assert c.check(0.1, 0.2, None) == "quiet"


def test_not_sure_never_alerts_and_the_checked_state_is_alert_only():
    p = {15: 0.1, 30: 0.4, 45: 0.9, 60: 0.5}
    rows = dict.fromkeys(p)
    pol = c.checked_policy("x", lambda r: p, 0.3, 0.5)
    assert pol.states(rows) == {15: False, 30: False, 45: True, 60: False}  # 0.4 and 0.5 are "not sure"
    assert not any(c.checked_policy("x", lambda r: p, 0.3, None).states(rows).values())
    student = c.checked_policy("s", lambda r: p, 0.3, -1.0)  # q below every p: same as the student alone
    assert [m for m, on in student.states(rows).items() if on] == [30, 45, 60]
    assert scan(pol, rows)[1] == [45]


def test_a_smaller_alpha_never_adds_an_alert():
    rng = SplitMix64(7)
    scores = [rng.next() / 2**64 for _ in range(500)]
    probes = [i / 200 for i in range(201)]
    previous = None
    for alpha in ("0.2", "0.1", "0.05", "0.02", "0.01", "0.005"):
        q = c.quantile(scores, alpha)[3]
        now = {p for p in probes if c.check(p, 0.3, q) == "alert"}
        assert previous is None or now <= previous
        previous = now


def test_coverage_of_exchangeable_data_over_2000_seeded_draws():
    n, alpha = 199, c.ALPHA
    j, _ = c.indices(n)
    rng, miss = SplitMix64(11), []
    for _ in range(2000):
        q = c.quantile([rng.next() / 2**64 for _ in range(n)], alpha)[3]
        miss.append(1 - q)  # P(U > q) for a new uniform score
    mean = sum(miss) / len(miss)
    sd = math.sqrt(sum((m - mean) ** 2 for m in miss) / (len(miss) - 1))
    assert mean <= float(alpha) + 3 * sd / math.sqrt(len(miss))
    assert mean >= float(alpha) - 3 * sd / math.sqrt(len(miss))
    beta_sd = math.sqrt(j * (n + 1 - j) / ((n + 1) ** 2 * (n + 2)))  # Beta(j, n + 1 - j)
    assert abs(sd - beta_sd) < 0.2 * beta_sd


def test_the_draw_is_one_bin_per_stay_deterministic_and_in_order():
    stays = [[0.1, 0.2, 0.3], [], [0.4], [0.5, 0.6], [0.7, 0.8, 0.9, 1.0]]
    d = c.draw(stays)
    assert d == c.draw(stays) and len(d) == 4  # the empty stay takes no draw
    rng = SplitMix64(c.DRAW_SEED)
    assert d == [s[rng.next() % len(s)] for s in stays if s]
    assert all(x in s for x, s in zip(d, [s for s in stays if s]))
    assert c.draw(stays, seed=1) != d or c.draw(stays, seed=2) != d  # the seed matters
    assert c.draw([]) == []


def test_the_verdict_rule_and_the_summary_points():
    d = c.spread("0.01", 3000)
    assert round(d, 4) == 0.0036
    assert c.verdict((0.001, 0.02), "0.01", 3000) == (True, False)
    assert c.verdict((0.01 + d + 1e-9, 0.03), "0.01", 3000) == (False, False)
    assert c.verdict((0.0, 0.01 - d - 1e-9), "0.01", 3000) == (True, True)
    assert c.verdict(None, "0.01", 3000) == (False, False)
    s = c.summary_quantiles([i / 1000 for i in reversed(range(1000))])
    assert len(s) == 200 and s[0] == 0 and s[-1] == 0.999 and s == sorted(s)
    assert c.summary_quantiles([3, 1, 2]) == [1, 2, 3]


def _cal(**kw):
    base = dict(draw_seed=c.DRAW_SEED, variant="all", seeds=[301, 302], negative_bins=40, t=0.25,
                onnx_sha256="a" * 64, days=30, speed=60, scenario_sha256="b" * 64)
    return c.make([i / 200 for i in range(199)], c.ALPHA, **(base | kw))


def test_the_calibration_file_round_trips_and_a_changed_byte_is_refused(tmp_path):
    cal = _cal()
    path = tmp_path / "calibration.json"
    h = c.save(cal, path)
    assert c.load(path) == (cal, h) and (tmp_path / "calibration.json.sha256").read_text() == h + "\n"
    assert cal.n == 199 and (cal.j, cal.k) == (2, 198) and cal.q == 197 / 200 and cal.binding
    first = path.read_bytes()
    c.save(cal, path)
    assert path.read_bytes() == first  # same bytes every time
    path.write_bytes(first.replace(b'"t": 0.25', b'"t": 0.26'))
    with pytest.raises(TrialError, match="sha256 differs"):
        c.load(path)


def _store(tmp_path, **changes):
    """A file whose sha256 sibling is right but whose content is wrong in one way."""
    obj = dataclasses.asdict(_cal()) | changes
    path = tmp_path / "x.json"
    data = json.dumps(obj, indent=1, sort_keys=True).encode()
    path.write_bytes(data)
    import hashlib
    path.with_name("x.json.sha256").write_text(hashlib.sha256(data).hexdigest() + "\n")
    return path


@pytest.mark.parametrize("changes, message", [
    ({"alpha": "0.05"}, "is not the fixed"), ({"draw_seed": 1}, "draw seed"), ({"extra": 1}, "unknown or missing"),
    ({"k": 197}, "do not follow from n"), ({"q": None}, "do not follow from n"), ({"n": "199"}, "wrong type"),
])
def test_load_refuses_a_wrong_alpha_an_unknown_field_and_inconsistent_numbers(tmp_path, changes, message):
    with pytest.raises(TrialError, match=message):
        c.load(_store(tmp_path, **changes))


def test_load_refuses_a_missing_sibling_and_an_oversize_file(tmp_path):
    path = _store(tmp_path)
    path.with_name("x.json.sha256").unlink()
    with pytest.raises(TrialError, match="cannot be read"):
        c.load(path)
    path.write_bytes(b" " * (c.MAX_BYTES + 1))
    path.with_name("x.json.sha256").write_text("0" * 64)
    with pytest.raises(TrialError, match="larger than"):
        c.load(path)


def test_verify_refuses_a_wrong_onnx_sha256_threshold_scenario_or_variant():
    cal, ok = _cal(), dict(variant="all", onnx_sha256="a" * 64, t=0.25, shape=(30, 60, "b" * 64))
    c.verify(cal, **ok)
    for bad, match in (({"onnx_sha256": "c" * 64}, "ONNX sha256"), ({"t": 0.3}, "threshold"),
                       ({"shape": (30, 60, "d" * 64)}, "scenario hash"), ({"shape": (29, 60, "b" * 64)}, "scenario hash"),
                       ({"variant": "no_temp"}, "variant")):
        with pytest.raises(TrialError, match=match):
            c.verify(cal, **(ok | bad))


def test_the_core_imports_no_numpy_and_the_constants_are_the_contract_s():
    text = Path(c.__file__).read_text()
    assert not [ln for ln in text.splitlines() if ln.startswith(("import numpy", "import onnx", "from .student"))]
    assert (c.ALPHA, c.DRAW_SEED, c.CAL_SEEDS, c.HELD_SEEDS) == ("0.01", 20261113, range(301, 341), range(11, 31))

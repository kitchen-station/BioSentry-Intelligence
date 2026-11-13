"""The reward policy: its file, the clipped z, the score and the persistence state machine
(INTEGRATION.md section 5)."""

import copy
import json
import math
from pathlib import Path

import pytest

from biosentry.domain.scoring.tier_b.reward_policy import (
    NAMES,
    Params,
    Policy,
    PolicyError,
    alert_states,
    bin_z,
    digest,
    parse_policy,
    score,
)

REPO = Path(__file__).resolve().parents[3]
FILE = REPO / "models" / "tier_b" / "reward_policy" / "policy.json"
REAL = json.loads(FILE.read_text("utf-8"))
NO_TEMP = json.loads((REPO / "models" / "tier_b" / "reward_policy" / "no_temperature" / "policy.json").read_text("utf-8"))
PARAMS = Params((0.614, 0.5247, 1.0638, 0.3149), 2.9776, 5)


def test_the_real_policy_holds_the_six_documented_numbers() -> None:
    pol = parse_policy(REAL)
    assert isinstance(pol, Policy) and pol.temperature is True
    assert pol.params == PARAMS
    assert pol.params.p == 5 and pol.params.theta == 2.9776
    assert pol.content_sha256 == "b364a680b989a659ae747f929806a65a9f828a0e412ba89d35ca72dd840f804f"


def test_the_no_temperature_policy_parses_only_as_no_temperature() -> None:
    pol = parse_policy(NO_TEMP, temperature=False)
    assert (pol.params.weights[3], pol.params.p, pol.params.theta) == (0.0, 3, 3.3703)
    with pytest.raises(PolicyError):
        parse_policy(NO_TEMP)  # the wired default is with temperature
    with pytest.raises(PolicyError):
        parse_policy(REAL, temperature=False)


def resealed(obj: dict) -> dict:
    """A body changed and given its new, correct content sha256: only the check under test can refuse it."""
    obj = copy.deepcopy(obj)
    obj.pop("sha256", None)
    obj["sha256"] = digest(obj)
    return obj


def edit(path: tuple, value: object, base: dict = REAL, seal: bool = True) -> dict:
    obj = copy.deepcopy(base)
    node = obj
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return resealed(obj) if seal else obj


def without(*keys: str) -> dict:
    obj = copy.deepcopy(REAL)
    for k in keys:
        obj.pop(k)
    return resealed(obj)


def reconfigured(**kw: object) -> dict:
    obj = copy.deepcopy(REAL)
    obj["config"] = {**obj["config"], **kw}
    obj["config_sha256"] = digest(obj["config"])
    return resealed(obj)


def test_a_no_temperature_policy_with_a_temperature_weight_is_refused() -> None:
    bad = edit(("params", "weights", "temperature"), 0.1, NO_TEMP)
    with pytest.raises(PolicyError, match="temperature weight"):
        parse_policy(bad, temperature=False)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {},
        {**REAL, "sha256": 5},
        edit(("params", "weights", "heart_rate"), 9.0, seal=False),  # a changed weight, the old sha256
        edit(("temperature",), False),
        edit(("name",), "other"),
        edit(("params", "weights", "heart_rate"), True),
        edit(("params", "weights", "heart_rate"), "0.6"),
        edit(("params", "weights", "heart_rate"), -1.0),
        edit(("params", "weights", "heart_rate"), 1e999),
        edit(("params", "theta"), 0.05),
        edit(("params", "theta"), "3"),
        edit(("params", "p"), 0),
        edit(("params", "p"), 9),
        edit(("params", "p"), True),
        edit(("params", "p"), 5.0),
        edit(("params", "extra"), 1),
        edit(("params", "weights", "extra"), 1.0),
        edit(("params",), []),
        edit(("params",), {"weights": [], "theta": 1.0, "p": 1}),
        without("params"),
        without("config"),
        without("config_sha256"),
        edit(("config_sha256",), "0" * 64),
        reconfigured(form="max"),
        reconfigured(extra=1),
        reconfigured(sigma0=0.3),
        reconfigured(sigma0=1),
        reconfigured(selection_trials=65),
        reconfigured(selection_trials=True),
        reconfigured(fix_theta=1),
        reconfigured(frozen_vital="pulse"),
        edit(("config",), []),
    ],
)
def test_a_bad_policy_file_is_refused(bad: object) -> None:
    with pytest.raises(PolicyError):
        parse_policy(bad)


def test_params_refuse_bad_values() -> None:
    for args in (((1.0, 1.0, 1.0), 3.0, 3), ((1.0, 1.0, 1.0, math.nan), 3.0, 3), ((1.0, 1.0, 1.0, 1.0), math.inf, 3),
                 ((1.0, 1.0, 1.0, 1.0), 3.0, 3.0)):
        with pytest.raises(PolicyError):
            Params(*args)  # type: ignore[arg-type]


def test_z_is_clipped_in_the_bad_direction() -> None:
    z = bin_z({"heart_rate": 2.5, "respiratory_rate": -1.0, "spo2": -3.0, "temperature": 0})
    assert z == (2.5, 0.0, 3.0, 0.0)  # spo2 falling is bad (-3 gives 3); rr falling is clipped to 0
    assert bin_z({"heart_rate": -2.0, "respiratory_rate": 0.0, "spo2": 4.0, "temperature": -0.1}) == (0.0,) * 4
    assert NAMES == ("heart_rate", "respiratory_rate", "spo2", "temperature")


def test_a_forming_baseline_or_missing_value_contributes_zero() -> None:
    assert bin_z({"heart_rate": None, "respiratory_rate": 1.0, "spo2": None, "temperature": 2.0}) == (0.0, 1.0, 0.0, 2.0)


@pytest.mark.parametrize("bad", [math.nan, math.inf, True, "1"])
def test_a_non_number_z_is_refused(bad: object) -> None:
    with pytest.raises(ValueError, match="finite number"):
        bin_z({"heart_rate": bad, "respiratory_rate": 0.0, "spo2": 0.0, "temperature": 0.0})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="finite number"):
        score(PARAMS, (bad, 0.0, 0.0, 0.0))  # type: ignore[arg-type]


def test_bin_z_needs_exactly_the_four_vitals() -> None:
    with pytest.raises(ValueError, match="exactly the vitals"):
        bin_z({"heart_rate": 1.0})
    with pytest.raises(ValueError, match="four values"):
        score(PARAMS, (1.0, 2.0))


def test_the_score_is_the_weighted_sum_by_hand() -> None:
    assert score(PARAMS, (0.0, 0.0, 0.0, 0.0)) == 0.0
    assert score(PARAMS, (1.0, 0.0, 0.0, 0.0)) == 0.614
    assert score(PARAMS, (2.0, 1.0, 3.0, 4.0)) == pytest.approx(1.228 + 0.5247 + 3.1914 + 1.2596)
    # exactly theta alerts: s >= theta, so z_spo2 = theta / w_spo2 on its own reaches it
    assert score(PARAMS, (0.0, 0.0, PARAMS.theta / PARAMS.weights[2], 0.0)) >= PARAMS.theta - 1e-12


P3 = Params((1.0, 1.0, 1.0, 0.0), 3.0, 3)


def run(bins: list[tuple[int, float | None]]) -> list[bool]:
    return alert_states(P3, bins)


def test_alerting_needs_p_consecutive_scored_bins_at_or_above_theta() -> None:
    hi = [(m, 3.0) for m in range(15, 15 * 6, 15)]  # 5 bins at exactly theta
    assert run(hi) == [False, False, True, True, True]
    assert run([(15, 2.99), (30, 3.0), (45, 3.0), (60, 3.0)]) == [False, False, False, True]
    assert run([(15, 3.0), (30, 3.0), (45, 2.0), (60, 3.0), (75, 3.0)]) == [False] * 5  # a low bin restarts


def test_a_gap_resets_the_run_and_the_bin_after_it_counts_one() -> None:
    bins = [(15, 3.0), (30, 3.0), (60, 3.0), (75, 3.0)]  # 45 is missing
    assert run(bins) == [False, False, False, False]
    assert run(bins + [(90, 3.0)]) == [False, False, False, False, True]  # 60, 75, 90 is a run of 3


def test_a_not_ranked_bin_resets_the_run_and_does_not_alert() -> None:
    assert run([(15, 3.0), (30, 3.0), (45, None), (60, 3.0), (75, 3.0)]) == [False] * 5
    assert run([(15, 3.0), (30, 3.0), (45, 3.0), (60, None), (75, 3.0)]) == [False, False, True, False, False]


def test_an_alert_ends_when_the_score_drops() -> None:
    assert run([(15, 5.0), (30, 5.0), (45, 5.0), (60, 1.0), (75, 5.0)]) == [False, False, True, False, False]


def test_the_state_machine_with_the_real_p_and_theta() -> None:
    pol = parse_policy(REAL).params
    s = pol.theta
    bins = [(15 * i, s) for i in range(1, 7)]
    assert alert_states(pol, bins) == [False] * 4 + [True, True]  # p = 5
    assert alert_states(pol, [(m, s - 0.001) for m, _ in bins]) == [False] * 6


@pytest.mark.parametrize(
    "bins",
    [[(15, 1.0), (15, 1.0)], [(30, 1.0), (15, 1.0)], [(15.0, 1.0)], [(True, 1.0)]],
)
def test_minutes_must_be_integers_in_strictly_increasing_order(bins: list) -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        run(bins)


def test_a_non_finite_score_is_refused() -> None:
    with pytest.raises(ValueError, match="finite number"):
        run([(15, math.nan)])
    assert run([]) == []

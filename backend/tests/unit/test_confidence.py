"""The confidence check and the calibration file's strict parser (INTEGRATION.md section 6, ruling 2)."""

import copy
import json
import math
from pathlib import Path

import pytest

from biosentry.domain.scoring.tier_c.confidence import (
    Calibration,
    CalibrationError,
    Check,
    binds,
    check,
    indices,
    parse_calibration,
)

REPO = Path(__file__).resolve().parents[3]
REAL = json.loads((REPO / "models" / "tier_c" / "confidence_check" / "calibration.json").read_text("utf-8"))
T, Q = 0.006, 0.019854247570037842


def test_the_check_names_are_the_three_documented_values() -> None:
    assert [c.value for c in Check] == ["alert", "not_sure", "quiet"]
    assert Check.NOT_SURE == "not_sure"


@pytest.mark.parametrize(
    ("p", "q", "expected"),
    [
        (T - 1e-9, Q, Check.QUIET),  # below t
        (0.0, Q, Check.QUIET),
        (T, Q, Check.NOT_SURE),  # p == t is not quiet
        ((T + Q) / 2, Q, Check.NOT_SURE),  # between t and q
        (Q, Q, Check.NOT_SURE),  # p == q: the comparison with q is strict
        (math.nextafter(Q, 1), Q, Check.ALERT),  # just above q
        (1.0, Q, Check.ALERT),
        (T - 1e-9, None, Check.QUIET),  # q absent: never alert
        (T, None, Check.NOT_SURE),
        (1.0, None, Check.NOT_SURE),
        (0.5, 0.004, Check.ALERT),  # q below t: still strictly above q and not below t
        (math.nan, Q, Check.NOT_SURE),  # NaN never alerts
        (math.nan, None, Check.NOT_SURE),
    ],
)
def test_the_check_truth_table(p: float, q: float | None, expected: Check) -> None:
    assert check(p, T, q) is expected


def test_indices_follow_the_real_file() -> None:
    assert indices(REAL["n"]) == (REAL["j"], REAL["k"]) == (32, 3207)
    assert indices(0) == (0, 1) and indices(98) == (0, 99) and indices(99) == (1, 99)


def test_the_real_calibration_parses() -> None:
    cal = parse_calibration(REAL)
    assert isinstance(cal, Calibration)
    assert (cal.q, cal.t, cal.alpha, cal.variant, cal.n, cal.j, cal.k) == (Q, T, "0.01", "all", 3238, 32, 3207)
    assert cal.seeds == tuple(range(301, 341))
    assert binds(cal, variant="all", onnx_sha256=REAL["onnx_sha256"], t=T)


def test_binding_refuses_any_difference() -> None:
    cal = parse_calibration(REAL)
    good = {"variant": "all", "onnx_sha256": REAL["onnx_sha256"], "t": T}
    assert binds(cal, **good)
    assert not binds(cal, **{**good, "variant": "no_temp"})
    assert not binds(cal, **{**good, "onnx_sha256": "0" * 64})
    assert not binds(cal, **{**good, "t": 0.007})
    assert not binds(cal, **{**good, "t": None})


def changed(**kw: object) -> dict:
    return {**copy.deepcopy(REAL), **kw}


def test_a_q_of_null_is_accepted_only_when_j_is_zero() -> None:
    cal = parse_calibration(changed(n=50, j=0, k=51, q=None))
    assert cal.q is None
    with pytest.raises(CalibrationError):
        parse_calibration(changed(q=None))  # j is 32
    with pytest.raises(CalibrationError):
        parse_calibration(changed(n=50, j=0, k=51))  # q set although j is 0


@pytest.mark.parametrize(
    "bad",
    [
        changed(extra=1),
        {k: v for k, v in REAL.items() if k != "speed"},
        changed(alpha="0.05"),
        changed(alpha=0.01),
        changed(draw_seed=1),
        changed(draw_seed=True),
        changed(n=-1, j=0, k=0, q=None),
        changed(n=3239),  # j, k no longer follow
        changed(j=31),
        changed(days=30.0),
        changed(speed=True),
        changed(variant=5),
        changed(onnx_sha256=None),
        changed(seeds="301"),
        changed(seeds=[301, True]),
        changed(t="0.006"),
        changed(t=True),
        changed(t=math.nan),
        changed(t=1.5),
        changed(q=-0.1),
        changed(q="0.02"),
        changed(q=math.inf),
        [],
        "x",
    ],
)
def test_a_malformed_calibration_is_refused(bad: object) -> None:
    with pytest.raises(CalibrationError):
        parse_calibration(bad)

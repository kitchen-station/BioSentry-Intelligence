"""The confidence check on the student (INTEGRATION.md section 6; port of research/confidence.py `check`,
`indices`, `Calibration`, `load`, `verify`). Pure: no file, no clock.

The student flags a bin only when `check(p, t, q) is Check.ALERT`. `not_sure` is a normal value, never a flag
and never "safe". The live path never claims a coverage certificate (ruling 4): nothing here tests for one.
Research code: synthetic data only, not validated.
"""

import math
from dataclasses import dataclass, fields
from enum import StrEnum
from fractions import Fraction

ALPHA = "0.01"  # fixed by research before any data was used
DRAW_SEED = 20261113
MAX_BYTES = 64 * 1024  # the calibration file's reader cap (section 2.2)


class Check(StrEnum):
    ALERT = "alert"
    NOT_SURE = "not_sure"
    QUIET = "quiet"


class CalibrationError(ValueError):
    pass


def check(p: float, t: float, q: float | None) -> Check:
    """Section 6.1; q None means +infinity. p < t: quiet. p >= t and q set and p > q (strict): alert.
    p >= t and (q None or p <= q): not_sure, so p == q is not_sure and p == t is never quiet.
    A NaN p is neither below t nor above q: not_sure, never alert."""
    if p < t:
        return Check.QUIET
    return Check.ALERT if q is not None and p > q else Check.NOT_SURE


def indices(n: int, alpha: str = ALPHA) -> tuple[int, int]:
    """(j, k): j = floor(alpha (n + 1)) in exact arithmetic, k = n + 1 - j."""
    j = int(Fraction(alpha) * (n + 1) // 1)
    return j, n + 1 - j


@dataclass(frozen=True, slots=True)
class Calibration:
    alpha: str
    days: int
    draw_seed: int
    j: int
    k: int
    n: int
    negative_bins: int
    onnx_sha256: str
    q: float | None  # None: +infinity, the check never alerts
    scenario_sha256: str
    seeds: tuple[int, ...]
    speed: int
    t: float
    variant: str


_INTS = ("days", "draw_seed", "j", "k", "n", "negative_bins", "speed")
_STRS = ("alpha", "onnx_sha256", "scenario_sha256", "variant")


def _unit(v: object) -> bool:
    return type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1  # type: ignore[operator]


def parse_calibration(obj: object) -> Calibration:
    """Strict: exactly the 14 keys, the exact types (no bool), alpha and draw seed the fixed ones, t and q
    in [0, 1], (j, k) equal to indices(n), q null exactly when j is 0. Anything else raises CalibrationError."""
    names = {f.name for f in fields(Calibration)}
    if not isinstance(obj, dict) or set(obj) != names:
        raise CalibrationError("unknown or missing field")
    if (any(type(obj[k]) is not int for k in _INTS) or any(type(obj[k]) is not str for k in _STRS)
            or not isinstance(obj["seeds"], list) or any(type(s) is not int for s in obj["seeds"])
            or not _unit(obj["t"]) or (obj["q"] is not None and not _unit(obj["q"]))):
        raise CalibrationError("a field has the wrong type or range")
    if obj["alpha"] != ALPHA or obj["draw_seed"] != DRAW_SEED:
        raise CalibrationError("alpha or draw seed is not the fixed value")
    if obj["n"] < 0 or indices(obj["n"]) != (obj["j"], obj["k"]) or (obj["q"] is None) != (obj["j"] == 0):
        raise CalibrationError("j, k or q do not follow from n")
    return Calibration(**{**obj, "seeds": tuple(obj["seeds"]), "t": float(obj["t"]),
                          "q": None if obj["q"] is None else float(obj["q"])})


def binds(cal: Calibration, *, variant: str, onnx_sha256: str, t: float | None) -> bool:
    """Section 6.3: the file belongs to this student's variant, ONNX sha256 and threshold, with no tolerance.
    A student with no threshold (None) is bound to nothing."""
    return cal.variant == variant and cal.onnx_sha256 == onnx_sha256 and t is not None and cal.t == t

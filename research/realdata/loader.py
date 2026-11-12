"""eICU-CRD Demo tables (gz csv, streamed) -> the harness's per-patient 15-minute rows.

Cohort: adult (age 18 or more, or "> 89"), the first unit stay of each `uniquepid` (smallest
`patientunitstayid`: ids are assigned in admission order, a judgement), unit stay of 12 hours or
more, and not (a) a vasopressor before hour 6 or (b) a death with "Comfort measures only" or "No CPR"
charted more than 12 hours before it.

Event: the first of (a) the first vasopressor infusion at hour 6 or later or (b) death in the unit.
It is predicted 12 hours ahead. Bins at or after the event are not built.

Grid: bin m (m = 0, 15, 30 ... minutes from unit admission, m before the stay's end or event) holds
the observations with m - 15 < t <= m. Harness time is m + ORIGIN, because the harness drops
admit minute 0 as a "starting patient". Repeated 5-minute heart rate, respiratory rate and SpO2 values
in a bin are reduced to their MEDIAN: robust to one artefact sample, and it never invents a value
outside the observed range. Systolic BP is the latest cuff or arterial value (its time kept as
`bp_minute`); temperature, GCS and the oxygen device are the latest charted value. Each has a staleness
limit (STALE_MINUTES); older means missing. Charted values are placed at their ENTRY time, so a bin
never reads something not yet entered. Nothing is interpolated or defaulted (I1).
"""

import csv
import gzip
import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median

from ..rescore import score_row
from ..trials import BIN_MINUTES, Episode, Row, Trial
from .source import RealDataError, verify
from .split import TEST_HOSPITALS, held_out_by_rule

from biosentry.domain.scoring.tier_a import News2Score

TABLES = ("patient", "vitalPeriodic", "vitalAperiodic", "nurseCharting", "respiratoryCharting",
          "infusiondrug", "carePlanGeneral")
ORIGIN = BIN_MINUTES
LOOKAHEAD = 720  # minutes: "event within the next 12 hours"
EARLY_VASOPRESSOR = 360  # a vasopressor before hour 6 leaves the stay out
MIN_STAY = 720
COMFORT_LEAD = 720
STALE_MINUTES = {"temperature": 240, "consciousness": 240, "oxygen": 240, "systolic": 60}

# Bounds: the backend's own, except temperature, held to 30-45 C (a Fahrenheit value left in a Celsius
# column is dropped, never converted by guesswork).
# The backend's: NEWS2 refuses a value below 0 or above 1000 (heart rate and respiratory rate
# may be exactly 0, a real reading), SpO2 above 100, and zero SpO2/temperature/systolic
# (`domain/scoring/tier_a/news2.py`). Systolic is also held to the API's input range
# `SYSTOLIC_RANGE` in `services/patients.py`. A value outside is dropped (missing), never clamped.
RANGES = {
    "heart_rate": (0.0, 1000.0), "respiratory_rate": (0.0, 1000.0), "spo2": (0.0001, 100.0),
    "temperature": (30.0, 45.0), "systolic": (20.0, 300.0),
}
_VASOPRESSOR = re.compile(r"norepinephrine|vasopressin|epinephrine|phenylephrine|dopamine")
_AIR = {"ra", "room air", "none (room air)", "none"}
_DEVICE = re.compile(r"vent|cannula|\bnc\b|bipap|bi-pap|cpap|mask|oximizer|oxymizer|trach|aerosol|high flow|"
                     r"hfnc|ambu|tent|non-rebreather|nrb|t-piece")
COMFORT = {"Comfort measures only", "No CPR"}


@dataclass
class Cohort:
    trial: Trial
    event_minute: dict[str, int | None]  # patient -> harness minute of the event
    counts: dict[str, int] = field(default_factory=dict)  # aggregates only


def table(folder: Path, name: str) -> Iterator[dict[str, str]]:
    with gzip.open(folder / f"{name}.csv.gz", "rt", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def _num(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def in_range(name: str, value: float | None) -> float | None:
    lo, hi = RANGES[name]
    return value if value is not None and lo <= value <= hi else None


def adult(age: str) -> bool:
    a = age.strip()
    return a == "> 89" or (a.isdigit() and int(a) >= 18)


def oxygen_status(text: str) -> bool | None:
    """Room air False; a recognised device True; anything unclear None (missing, not guessed)."""
    t = text.strip().lower()
    if t in _AIR:
        return False
    return True if _DEVICE.search(t) else None


def consciousness_from_gcs(text: str) -> str | None:
    """GCS total 15 is "A"; 3 to 14 is "not A" (the letter C stands for any non-A: NEWS2 scores them all 3)."""
    value = _num(text)
    if value is None or value != int(value) or not 3 <= value <= 15:
        return None
    return "A" if value == 15 else "C"


def _latest(series: list[tuple[float, object]], m: int, stale: int) -> tuple[float, object] | None:
    i = bisect_right(series, m, key=lambda x: x[0])
    return series[i - 1] if i and m - series[i - 1][0] <= stale else None


def build_rows(stay: str, end: int, s: dict[str, list]) -> list[Row]:
    """Rows for bins m = 0, 15 ... below `end` (minutes), from sorted (time, value) series."""
    rows = []
    for m in range(0, end, BIN_MINUTES):
        got = {}
        for v in ("heart_rate", "respiratory_rate", "spo2"):
            times = s[v]
            lo, hi = bisect_right(times, m - BIN_MINUTES, key=lambda x: x[0]), bisect_right(times, m, key=lambda x: x[0])
            got[v] = median(x[1] for x in times[lo:hi]) if hi > lo else None
        sbp = _latest(s["systolic"], m, STALE_MINUTES["systolic"])
        temp = _latest(s["temperature"], m, STALE_MINUTES["temperature"])
        cons = _latest(s["consciousness"], m, STALE_MINUTES["consciousness"])
        oxy = _latest(s["oxygen"], m, STALE_MINUTES["oxygen"])
        row = Row(stay, m + ORIGIN, got["heart_rate"], got["respiratory_rate"], got["spo2"],
                  None if temp is None else temp[1], None if sbp is None else sbp[1][0],
                  None if sbp is None else sbp[0] + ORIGIN, None if cons is None else cons[1],
                  None if oxy is None else oxy[1], 1, False, None)
        score = score_row(row)
        if isinstance(score, News2Score):
            row = replace(row, ranked=True, news2=score.total)
        rows.append(row)
    return rows


def check_test_split(folder: str | Path) -> None:
    """The encoded test list must equal the sim fit's rule applied to this dataset's hospital ids."""
    verify(Path(folder), tuple(f"{t}.csv.gz" for t in TABLES))
    ids = {int(r["hospitalid"]) for r in table(Path(folder), "patient")}
    if held_out_by_rule(ids) != TEST_HOSPITALS:
        raise RealDataError("the held-out hospital list does not match the rule applied to this dataset")


def load_cohort(folder: str | Path, hospitals: frozenset[int]) -> Cohort:
    """Stays of `hospitals` only. Checksums are verified first; any difference refuses."""
    folder = Path(folder)
    verify(folder, tuple(f"{t}.csv.gz" for t in TABLES))
    counts: dict[str, int] = defaultdict(int)

    allp = list(table(folder, "patient"))
    first: dict[str, int] = {}
    for r in allp:
        sid = int(r["patientunitstayid"])
        if r["uniquepid"] not in first or sid < first[r["uniquepid"]]:
            first[r["uniquepid"]] = sid
    cand: dict[int, dict[str, str]] = {}
    for r in allp:
        if int(r["hospitalid"]) not in hospitals:
            continue
        counts["stays in these hospitals"] += 1
        leave = _num(r["unitdischargeoffset"])
        if first[r["uniquepid"]] != int(r["patientunitstayid"]):
            counts["not the first stay of the patient"] += 1
        elif not adult(r["age"]):
            counts["not adult"] += 1
        elif leave is None or leave < MIN_STAY:
            counts["unit stay under 12 hours"] += 1
        else:
            cand[int(r["patientunitstayid"])] = r

    vaso: dict[int, int] = {}
    for r in table(folder, "infusiondrug"):
        sid, off = int(r["patientunitstayid"]), _num(r["infusionoffset"])
        if sid in cand and off is not None and _VASOPRESSOR.search(r["drugname"].lower()):
            vaso[sid] = min(vaso.get(sid, int(off)), int(off))
    comfort: dict[int, int] = {}
    for r in table(folder, "carePlanGeneral"):
        sid, off = int(r["patientunitstayid"]), _num(r["cplitemoffset"])
        if sid in cand and r["cplitemvalue"] in COMFORT and off is not None:
            comfort[sid] = min(comfort.get(sid, int(off)), int(off))

    keep: dict[int, tuple[int, str | None]] = {}  # stay -> (end offset, event kind or None)
    for sid, r in sorted(cand.items()):
        leave, died = int(float(r["unitdischargeoffset"])), r["unitdischargestatus"] == "Expired"
        if sid in vaso and vaso[sid] < EARLY_VASOPRESSOR:
            counts["vasopressor before hour 6"] += 1
        elif died and sid in comfort and comfort[sid] < leave - COMFORT_LEAD:
            counts["comfort or no-CPR death"] += 1
        else:
            v = vaso.get(sid)
            if v is not None and v < leave:
                keep[sid] = (v, "vasopressor")
            elif died:
                keep[sid] = (leave, "death")
            else:
                keep[sid] = (leave, None)

    series: dict[int, dict[str, list]] = {sid: defaultdict(list) for sid in keep}

    def add(sid: int, name: str, t: float | None, v: object) -> None:
        if v is not None and t is not None and sid in series:
            series[sid][name].append((t, v))

    for r in table(folder, "vitalPeriodic"):
        sid = int(r["patientunitstayid"])
        if sid not in keep:
            continue
        t = _num(r["observationoffset"])
        for name, col in (("heart_rate", "heartrate"), ("respiratory_rate", "respiration"), ("spo2", "sao2"),
                          ("temperature", "temperature"), ("systolic", "systemicsystolic")):
            raw = _num(r[col])
            val = in_range(name, raw)
            counts[f"dropped out of range: {name}"] += raw is not None and val is None
            add(sid, name, t, (val, 0) if name == "systolic" and val is not None else val)
    for r in table(folder, "vitalAperiodic"):
        sid = int(r["patientunitstayid"])
        raw = _num(r["noninvasivesystolic"])
        val = in_range("systolic", raw)
        if sid in keep and raw is not None:
            counts["dropped out of range: systolic"] += val is None
            add(sid, "systolic", _num(r["observationoffset"]), None if val is None else (val, 1))
    for r in table(folder, "nurseCharting"):
        sid = int(r["patientunitstayid"])
        if sid not in keep:
            continue
        name, t = r["nursingchartcelltypevalname"], _num(r["nursingchartentryoffset"])
        if name == "Temperature (C)":
            raw = _num(r["nursingchartvalue"])
            val = in_range("temperature", raw)
            counts["dropped out of range: temperature"] += raw is not None and val is None
            add(sid, "temperature", t, val)
        elif name == "GCS Total":
            add(sid, "consciousness", t, consciousness_from_gcs(r["nursingchartvalue"]))
        elif name == "O2 Admin Device":
            add(sid, "oxygen", t, oxygen_status(r["nursingchartvalue"]))
    for r in table(folder, "respiratoryCharting"):
        sid = int(r["patientunitstayid"])
        if sid in keep and r["respchartvaluelabel"] in ("O2 Device", "Oxygen Delivery Method"):
            add(sid, "oxygen", _num(r["respchartentryoffset"]), oxygen_status(r["respchartvalue"]))

    rows: list[Row] = []
    episodes: list[Episode] = []
    event_minute: dict[str, int | None] = {}
    patient_days = 0.0
    for sid, (end, kind) in sorted(keep.items()):
        p = str(sid)
        s = {k: sorted(series[sid].get(k, []), key=lambda x: x[0]) for k in
             ("heart_rate", "respiratory_rate", "spo2", "temperature", "systolic", "consciousness", "oxygen")}
        # systolic: same time, cuff (1) sorts after arterial (0), so the latest-at-a-time is the cuff
        s["systolic"].sort(key=lambda x: (x[0], x[1][1]))
        rows += build_rows(p, end, s)
        patient_days += end / 1440
        full_in = end + ORIGIN
        if kind is None:
            episodes.append(Episode(p, ORIGIN, full_in, None, None, None))
            event_minute[p] = None
        else:
            start = max(full_in - LOOKAHEAD, ORIGIN)
            counts["events with fewer than 12 hours of look-back"] += full_in - LOOKAHEAD < ORIGIN
            episodes.append(Episode(p, ORIGIN, full_in, kind, start, full_in - start))
            event_minute[p] = full_in
            counts[f"events: {kind}"] += 1
    days = max((e.leave_minute for e in episodes), default=0) // 1440 + 2
    counts["stays kept"] = len(keep)
    counts["bins"] = len(rows)
    trial = Trial("eicu-crd-demo", 0, days, 1, "0" * 64, "unknown", 0, patient_days, tuple(rows), tuple(episodes))
    return Cohort(trial, event_minute, dict(counts))

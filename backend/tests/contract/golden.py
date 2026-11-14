"""The models team's reference file (`research/tests/data/wiring/golden.json`) turned into what the backend stores:
15-minute bins at their minute (the END of the window, as the simulator posts them) and charted observations at
their own times. The file's sha256 is ONE constant: change it here when the file is regenerated."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from biosentry.repositories.postgres import StoredObservation
from biosentry.schemas import VitalHistory

GOLDEN = Path(__file__).resolve().parents[3] / "research" / "tests" / "data" / "wiring" / "golden.json"
GOLDEN_SHA256 = "0c753761934f38205d2717bfdb40d8dd0d50e633ff544cef3cc84e492d3b7b31"
ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)
VITALS = ("heart_rate", "respiratory_rate", "spo2", "temperature")


def load() -> dict[str, Any]:
    raw = GOLDEN.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == GOLDEN_SHA256, "the reference file changed: re-pin GOLDEN_SHA256"
    doc: dict[str, Any] = json.loads(raw)
    assert doc["format"] == "biosentry-wiring-golden/1"
    return doc


def at(minute: float) -> datetime:
    return ORIGIN + timedelta(minutes=minute)


def points(stay: dict[str, Any], upto: int | None = None) -> dict[str, list[VitalHistory]]:
    """Each vital's stored bins of the stay (a gap has no row), up to the bin with this index."""
    bins = stay["bins"] if upto is None else stay["bins"][: upto + 1]
    return {
        v: [VitalHistory.model_construct(time=at(b["minute"]), value=b["row"][v]) for b in bins if b["row"]]
        for v in VITALS
    }


def charted_events(stay: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    """(code, minute, value) for each change of a charted input. An empty value is an absent row. Blood
    pressure is stored at its own cuff minute; the others at the minute of the bin that shows the change."""
    last: dict[str, Any] = {}
    out: list[tuple[str, int, dict[str, Any]]] = []
    for b in stay["bins"]:
        r, m = b["row"], b["minute"]
        if r is None:
            continue
        sys_bp = r["systolic"]
        want = {
            "blood_pressure": (int(r["bp_minute"]) if sys_bp is not None else m,
                               {} if sys_bp is None else {"num": sys_bp, "num2": 80.0}),
            "consciousness": (m, {} if r["consciousness"] is None else {"txt": r["consciousness"]}),
            "supplemental_o2": (m, {"num": float(r["on_oxygen"])}),
            "spo2_scale": (m, {"num": float(r["spo2_scale"])}),
        }
        for code, (minute, value) in want.items():
            key = (minute if code == "blood_pressure" else 0, repr(value))
            if last.get(code) != key:
                last[code] = key
                out.append((code, minute, value))
    return sorted(out, key=lambda e: e[1])


def stored_observations(stay: dict[str, Any]) -> list[StoredObservation]:
    rows = []
    for i, (code, minute, value) in enumerate(charted_events(stay), 1):
        rows.append(StoredObservation(
            stay["id"], code, "nurse", at(minute), i, "ok" if value else "missing",
            value.get("num"), value.get("num2"), value.get("txt"), None if value else "not_recorded"))
    return rows

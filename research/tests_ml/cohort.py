"""A tiny synthetic cohort, built by arithmetic (no random source), for the student tests."""

from dataclasses import replace

from research.rescore import score_row
from research.trials import Episode, Row, Trial

BINS = 120  # 30 h of 15-minute bins per stay


def scored(r: Row) -> Row:
    """Set ranked and news2 the way the sim would (by the backend's own NEWS2)."""
    s = score_row(r)
    total = getattr(s, "total", None)
    return replace(r, ranked=total is not None, news2=total)


def vitals_row(patient: str, minute: int, hr: float, rr: float = 16, spo2: float = 97,
               temp: float = 36.8) -> Row:
    return scored(Row(patient, minute, hr, rr, spo2, temp, 120, None, "A", False, 1, False, None))


def stay(patient: str, i: int, seed: int, episode: bool) -> tuple[dict[int, Row], Episode]:
    start = 15 * (60 + (i * 7 + seed) % 20) + 0.5 if episode else None
    ramp = 120.0 if episode else None
    rows = {}
    for b in range(1, BINS + 1):
        m = 15 * b
        w = 0.0 if not episode or m < start else min(1.0, (m - start) / ramp)
        wobble = ((m // 15) * 7 + i + seed) % 5 - 2  # small deterministic noise
        rows[m] = vitals_row(patient, m, 70 + i % 4 + wobble + 35 * w, 16 + wobble % 2 + 6 * w,
                             97 - 3 * w, 36.8 + 0.6 * w)
    return rows, Episode(patient, 15, None, "respiratory" if episode else None, start, ramp)


def trial(seed: int, patients: int = 8) -> Trial:
    rows, episodes = [], []
    for i in range(patients):
        r, e = stay(f"p-{i:03d}", i, seed, episode=i % 2 == 0)
        rows += r.values()
        episodes.append(e)
    return Trial(f"tiny-{seed}", seed, 2, 60, "0" * 64, "abcdef1", 22, patients * BINS * 15 / 1440,
                 tuple(rows), tuple(episodes))

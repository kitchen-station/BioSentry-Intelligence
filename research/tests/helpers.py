"""Shared builders for the tests. Nothing here reads the clock, the network or a random source."""

import shutil
from pathlib import Path

from research.trials import Episode, Row, Trial

TOY = Path(__file__).parent / "data" / "toy"

# Hand-checked NEWS2 inputs (RCP 2017, scale 1, on air, alert, sbp 120, temp 36.8):
#   N0 total 0 | M3 hr 95 (1) + rr 22 (2) = 3, no single 3 | S3 spo2 91 (3) = 3, one single 3
#   T5 hr 115 (2) + rr 22 (2) + spo2 95 (1) = 5 | N9 hr 135 (3) + rr 26 (3) + spo2 91 (3) = 9
#   NR spo2 missing, not ranked
STATES = {
    "N0": (70, 16, 97, 0, True),
    "M3": (95, 22, 97, 3, True),
    "S3": (70, 16, 91, 3, True),
    "T5": (115, 22, 95, 5, True),
    "N9": (135, 26, 91, 9, True),
    "NR": (70, 16, None, None, False),
}


def row(patient: str, minute: int, state: str, bp_minute: float | None = None) -> Row:
    hr, rr, spo2, news2, ranked = STATES[state]
    return Row(patient, minute, hr, rr, spo2, 36.8, 120, bp_minute, "A", False, 1, ranked, news2)


def rows(patient: str, states: dict[int, str]) -> dict[int, Row]:
    return {m: row(patient, m, s) for m, s in states.items()}


def trial(rows_: list[Row], episodes: list[Episode], *, days: int = 1, seed: int = 11,
          patient_days: float = 1.0) -> Trial:
    return Trial("mem", seed, days, 60, "0" * 64, "abcdef1", 22, patient_days, tuple(rows_),
                 tuple(episodes))


def copy_toy(dest: Path, **edits: tuple[str, str]) -> Path:
    """Copy the toy folder, applying {file: (old, new)} text replacements (old must be present)."""
    shutil.copytree(TOY, dest)
    for name, (old, new) in edits.items():
        p = dest / name
        text = p.read_text()
        assert old in text, (name, old)
        p.write_text(text.replace(old, new, 1))
    return dest

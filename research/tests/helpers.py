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


def table_cells(text: str) -> dict[str, dict[str, str]]:
    """{policy: {row label: printed cell}} from the table `research.evaluate` prints (policy columns are 28
    wide; the last name can be wider). Only for tests: the exporter never reads a text report."""
    lines = text.split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("alerts per patient per 12-hour shift"))
    head = lines[i - 1]
    w = len(head) - len(head.lstrip())
    n = -(-(len(lines[i]) - w) // 28)
    names = [head[w + 28 * k: w + 28 * (k + 1)].strip() for k in range(n - 1)] + [head[w + 28 * (n - 1):].strip()]
    out: dict[str, dict[str, str]] = {name: {} for name in names}
    seen: dict[str, int] = {}
    for ln in lines[i:]:
        if not ln.strip():
            break
        label = ln[:w].rstrip()
        seen[label] = seen.get(label, 0) + 1
        key = label if seen[label] == 1 else f"{label}#{seen[label]}"  # the interval label repeats
        for k, name in enumerate(names):
            out[name][key] = ln[w + 28 * k: w + 28 * (k + 1)].strip()
    return out


def check_rules_against_text(rules: list[dict], text: str) -> None:
    """Every number of the held-out JSON rules that the table prints equals the printed cell."""
    cells = table_cells(text)
    frac = lambda f: "n/a" if f["d"] == 0 else f"{f['rate']:.3f} ({f['n']}/{f['d']})"  # noqa: E731
    ivl = lambda f: "n/a" if f["lo"] is None else f"{f['lo']:.3f} to {f['hi']:.3f}"  # noqa: E731
    assert [r["id"] for r in rules] == list(cells)
    for r in rules:
        c = cells[r["id"]]
        iv = "  95% interval (patient-level bootstrap)"
        assert c["alerts per patient per 12-hour shift"] == f"{r['per_shift']['value']:.3f}"
        assert c[iv] == ivl(r["per_shift"]) and c[iv + "#2"] == ivl(r["new_alert"]) and c[iv + "#3"] == ivl(r["any_bin"])
        assert c["time in alert (patient-bins alerting / patient-bins)"] == frac(r["time_in_alert"])
        assert c["episodes with a NEW alert in the window / evaluated"] == frac(r["new_alert"])
        assert c["episodes ALERTING in any bin of the window / evaluated"] == frac(r["any_bin"])
        assert c["episodes ALERTING in any bin / episodes not cut short"] == frac(r["any_bin_not_cut_short"])
        assert c["chance: control windows with a NEW alert / control windows"] == frac(r["chance_new"])
        assert c["chance: control windows ALERTING in any bin / control windows"] == frac(r["chance_any"])
        ex = r["excess_any_not_cut_short"]
        assert c["excess over chance, ALERTING in any bin, episodes not cut short"] == ("n/a" if ex is None else f"{ex:+.3f}")
        lead = r["lead"]
        assert c["lead time median, minutes (episodes with a new alert)"] == ("n/a" if lead["median"] is None else f"{lead['median']:.1f}")
        assert c["lead time interquartile range, minutes"] == ("n/a" if lead["q1"] is None else f"{lead['q1']:.1f} to {lead['q3']:.1f}")

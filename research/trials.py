"""Reader for one trial folder, format version 1 (the contract is the sim repo's trial-format doc).

It opens exactly four fixed file names, parses only with int/float/str/json.loads (nothing is
evaluated or executed), and refuses anything off-contract with one clear TrialError.
"""

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

FORMAT_VERSION = 1
BIN_MINUTES = 15
FILES = ("manifest.json", "cohort.csv", "episodes.csv", "summary.json")

# Caps: a 60-day trial (the format's maximum) is about 4 MB and 60,000 cohort rows.
MAX_JSON_BYTES = 64 * 1024
MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_LINE_CHARS = 1000
MAX_COHORT_ROWS = 1_000_000
MAX_EPISODE_ROWS = 100_000

COHORT_HEADER = (
    "patient,minute,heart_rate,respiratory_rate,spo2,temperature,systolic,diastolic,"
    "bp_minute,consciousness,on_oxygen,spo2_scale,coverage,ranked,news2"
)
EPISODES_HEADER = (
    "patient,admit_minute,leave_minute,outcome,episode_kind,episode_start,ramp_minutes,"
    "af_start,af_end,persistent_af"
)
KINDS = ("respiratory", "tachycardic", "hypotensive")
_NUMBER = re.compile(r"-?[0-9]{1,9}(\.[0-9]{1,17})?")  # [0-9]: \d would accept other scripts
_INTEGER = re.compile(r"-?[0-9]{1,9}")
_PATIENT = re.compile(r"[A-Za-z0-9-]{1,16}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{7,40}|unknown")


class TrialError(ValueError):
    """The one error the reader raises. The message is a single line."""


def show(text: object) -> str:
    """Echo untrusted text: capped at 40 characters, anything unprintable replaced by '?'."""
    s = str(text)[:40]
    return "".join(c if c.isprintable() else "?" for c in s)


@dataclass(frozen=True, slots=True)
class Row:
    patient: str
    minute: int
    heart_rate: float | None
    respiratory_rate: float | None
    spo2: float | None
    temperature: float | None
    systolic: float | None
    bp_minute: float | None
    consciousness: str
    on_oxygen: bool
    spo2_scale: int
    ranked: bool
    news2: int | None


@dataclass(frozen=True, slots=True)
class Episode:
    patient: str
    admit_minute: int
    leave_minute: int | None
    kind: str | None
    start: float | None
    ramp_minutes: float | None


@dataclass(frozen=True, slots=True)
class Trial:
    name: str
    seed: int
    days: int
    speed: int
    scenario_sha256: str
    sim_commit: str
    node_major: int
    patient_days: float
    rows: tuple[Row, ...]
    episodes: tuple[Episode, ...]


def _read(folder: Path, name: str, cap: int) -> str:
    path = folder / name
    try:
        # O_NOFOLLOW: a symlinked trial file is refused, never followed.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise TrialError(f"{name}: missing") from None
    except OSError:
        raise TrialError(f"{name}: cannot be opened (a symlink is refused)") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):  # a directory or FIFO: before fdopen wraps it
            raise TrialError(f"{name}: not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(cap + 1)
    except OSError:
        raise TrialError(f"{name}: cannot be read") from None
    finally:
        os.close(fd)
    if len(data) > cap:
        raise TrialError(f"{name}: larger than the {cap} byte cap")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise TrialError(f"{name}: not valid UTF-8") from None


def _json(folder: Path, name: str) -> dict:
    text = _read(folder, name, MAX_JSON_BYTES)
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):  # bad syntax, a huge integer, or nesting too deep
        raise TrialError(f"{name}: not valid JSON (or a number or nesting too large)") from None
    if not isinstance(obj, dict):
        raise TrialError(f"{name}: not a JSON object")
    return obj


def _int_field(obj: dict, key: str, file: str, lo: int, hi: int) -> int:
    v = obj.get(key)
    if type(v) is not int or not lo <= v <= hi:  # bool is not an int here
        raise TrialError(f"{file}: {key} must be an integer from {lo} to {hi}")
    return v


def _lines(text: str, name: str, header: str, max_rows: int) -> list[str]:
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != header:
        raise TrialError(f"{name}: wrong header")
    if len(lines) - 1 > max_rows:
        raise TrialError(f"{name}: more than {max_rows} rows")
    for n, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_CHARS:
            raise TrialError(f"{name} line {n}: longer than {MAX_LINE_CHARS} characters")
    return lines[1:]


def _num(s: str, file: str, line: int, col: str, *, integer: bool = False) -> float | None:
    if s == "":
        return None
    if not (_INTEGER if integer else _NUMBER).fullmatch(s):
        raise TrialError(f"{file} line {line}: {col} is not a number: {show(s)!r}")
    return int(s) if integer else float(s)


def _choice(s: str, options: tuple[str, ...], file: str, line: int, col: str) -> str:
    if s not in options:
        raise TrialError(f"{file} line {line}: {col} not one of {'/'.join(options)}: {show(s)!r}")
    return s


def _patient(s: str, file: str, line: int) -> str:
    if not _PATIENT.fullmatch(s):
        raise TrialError(f"{file} line {line}: bad patient id {show(s)!r}")
    return s


def _episodes(text: str) -> tuple[Episode, ...]:
    out: list[Episode] = []
    seen: set[str] = set()
    for n, line in enumerate(_lines(text, "episodes.csv", EPISODES_HEADER, MAX_EPISODE_ROWS), 2):
        f = line.split(",")
        if len(f) != 10:
            raise TrialError(f"episodes.csv line {n}: expected 10 fields")
        patient = _patient(f[0], "episodes.csv", n)
        if patient in seen:
            raise TrialError(f"episodes.csv line {n}: patient {patient} twice")
        seen.add(patient)
        admit = _num(f[1], "episodes.csv", n, "admit_minute", integer=True)
        leave = _num(f[2], "episodes.csv", n, "leave_minute", integer=True)
        if admit is None:
            raise TrialError(f"episodes.csv line {n}: admit_minute is empty")
        if f[3]:
            _choice(f[3], ("discharged", "higher_care"), "episodes.csv", n, "outcome")
        kind = _choice(f[4], KINDS, "episodes.csv", n, "episode_kind") if f[4] else None
        start = _num(f[5], "episodes.csv", n, "episode_start")
        ramp = _num(f[6], "episodes.csv", n, "ramp_minutes")
        if (kind is None) != (start is None) or (kind is None) != (ramp is None):
            raise TrialError(f"episodes.csv line {n}: episode fields must be all set or all empty")
        out.append(Episode(patient, int(admit), None if leave is None else int(leave), kind, start, ramp))
    return tuple(out)


def _cohort(text: str, days: int) -> tuple[Row, ...]:
    out: list[Row] = []
    seen: set[tuple[str, int]] = set()
    for n, line in enumerate(_lines(text, "cohort.csv", COHORT_HEADER, MAX_COHORT_ROWS), 2):
        f = line.split(",")
        if len(f) != 15:
            raise TrialError(f"cohort.csv line {n}: expected 15 fields")
        patient = _patient(f[0], "cohort.csv", n)
        minute = _num(f[1], "cohort.csv", n, "minute", integer=True)
        if minute is None or minute % BIN_MINUTES or not 0 < minute < days * 1440:
            raise TrialError(
                f"cohort.csv line {n}: minute must be a multiple of {BIN_MINUTES} inside the trial"
            )
        if (patient, minute) in seen:
            raise TrialError(f"cohort.csv line {n}: {patient} minute {minute} twice")
        seen.add((patient, minute))
        v = [_num(f[i], "cohort.csv", n, c) for i, c in ((2, "heart_rate"), (3, "respiratory_rate"),
             (4, "spo2"), (5, "temperature"), (6, "systolic"), (8, "bp_minute"))]
        for i, c in ((7, "diastolic"), (12, "coverage")):
            _num(f[i], "cohort.csv", n, c)  # validated, not used
        level = _choice(f[9], ("A", "C", "V", "P", "U"), "cohort.csv", n, "consciousness")
        oxygen = _choice(f[10], ("0", "1"), "cohort.csv", n, "on_oxygen")
        scale = _choice(f[11], ("1", "2"), "cohort.csv", n, "spo2_scale")
        ranked = _choice(f[13], ("0", "1"), "cohort.csv", n, "ranked")
        news2 = _num(f[14], "cohort.csv", n, "news2", integer=True)
        if (ranked == "1") != (news2 is not None):
            raise TrialError(f"cohort.csv line {n}: news2 must be set exactly when ranked is 1")
        out.append(Row(patient, int(minute), *v, level, oxygen == "1", int(scale), ranked == "1",
                       None if news2 is None else int(news2)))
    return tuple(out)


def load_trial(folder: str | os.PathLike) -> Trial:
    """Read one trial folder. The label used in errors is the folder's own name."""
    folder = Path(folder)
    if not folder.is_dir():
        raise TrialError(f"{show(folder.name)}: not a folder")
    label = show(folder.name)
    try:
        return _load(folder, label)
    except TrialError as e:
        raise TrialError(f"{label}: {e}") from None


def _load(folder: Path, label: str) -> Trial:
    manifest = _json(folder, "manifest.json")
    if manifest.get("format_version") != FORMAT_VERSION or type(manifest["format_version"]) is not int:
        raise TrialError(f"manifest.json: unknown format_version {show(manifest.get('format_version'))!r}")
    if manifest.get("synthetic") is not True:
        raise TrialError("manifest.json: synthetic is not true")
    days = _int_field(manifest, "days", "manifest.json", 1, 60)
    speed = _int_field(manifest, "speed", "manifest.json", 1, 100_000)
    seed = _int_field(manifest, "seed", "manifest.json", 0, 2**31 - 1)
    node_major = _int_field(manifest, "node_major", "manifest.json", 0, 1000)
    n_patients = _int_field(manifest, "patients", "manifest.json", 0, MAX_EPISODE_ROWS)
    n_episodes = _int_field(manifest, "episodes", "manifest.json", 0, MAX_EPISODE_ROWS)
    if _int_field(manifest, "bin_minutes", "manifest.json", 1, 1440) != BIN_MINUTES:
        raise TrialError(f"manifest.json: bin_minutes is not {BIN_MINUTES}")
    sha, commit = manifest.get("scenario_sha256"), manifest.get("sim_commit")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        raise TrialError("manifest.json: scenario_sha256 is not 64 hex characters")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise TrialError("manifest.json: sim_commit is not 7 to 40 hex characters or 'unknown'")

    summary = _json(folder, "summary.json")
    days_in_ward = summary.get("patient_days")
    if type(days_in_ward) not in (int, float) or not 0 <= days_in_ward < 1e9:
        raise TrialError("summary.json: patient_days must be a non-negative number")

    episodes = _episodes(_read(folder, "episodes.csv", MAX_CSV_BYTES))
    rows = _cohort(_read(folder, "cohort.csv", MAX_CSV_BYTES), days)
    if len(episodes) != n_patients:
        raise TrialError("manifest.json: patients does not match the rows of episodes.csv")
    if sum(e.kind is not None for e in episodes) != n_episodes:
        raise TrialError("manifest.json: episodes does not match the episode rows of episodes.csv")
    known = {e.patient for e in episodes}
    for r in rows:
        if r.patient not in known:
            raise TrialError(f"cohort.csv: patient {r.patient} is not in episodes.csv")
    return Trial(label, seed, days, speed, sha, commit, node_major, float(days_in_ward), rows, episodes)

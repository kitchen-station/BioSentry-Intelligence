"""The append-only ledger, the proposals, and the human sign-off.

Status is never stored: it is read from the records that follow a proposal. `apply` is the only
code in this package that writes under the models folder.
"""

import fcntl
import getpass
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from research import reward_loop
from research.trials import TrialError, show

from .config import AgentError, Config

LEDGER = "ledger.jsonl"
MAX_LINE = 256 * 1024
MAX_FILE = 64 * 1024 * 1024
MAX_SOURCE = 1024 * 1024
MAX_GENERATIONS = 64
ZERO = "0" * 64
ACTIONS = ("adopt_reward_policy", "set_baseline_params")
_NAME = re.compile(r"[A-Za-z][A-Za-z .'-]{1,59}")
_RUN = re.compile(r"runs/S-[0-9]{4}")
_POLICY = re.compile(r"runs/S-[0-9]{4}/reward_policy(/no_temperature)?/(policy\.json|generations/gen-[0-9]{2}\.json)")
_PARAMS = re.compile(r"runs/S-[0-9]{4}/params\.json")


# The keys the readers of each record type rely on. A record without them is a broken ledger.
_NEEDS = {"start": ("id", "kind", "plan"), "step": ("id", "kind", "plan", "calls"),
          "proposal": ("id", "action", "step", "files"), "approval": ("proposal", "by"),
          "applied": ("proposal", "files"), "apply_failed": ("proposal", "files")}


class LedgerError(AgentError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean(text: object, cap: int) -> str:
    """Untrusted text: capped, and anything unprintable replaced by '?'."""
    return "".join(c if c.isprintable() else "?" for c in str(text)[:cap])


def _dump(rec: dict) -> bytes:
    return json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()


# --- the file ---------------------------------------------------------------------------------------

def _slurp(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    if size > MAX_FILE:
        raise LedgerError("ledger is larger than 64 MiB")
    return os.pread(fd, size, 0)


def _check(data: bytes) -> tuple[list[dict], str]:
    """The records and the hash of the last line. Any break is refused, with its sequence number."""
    if data and not data.endswith(b"\n"):
        raise LedgerError(f"ledger broken after seq {data.count(10)}: last line is incomplete")
    records, prev = [], ZERO
    for n, line in enumerate(data.split(b"\n")[:-1], 1):
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):
            rec = None
        ok = (isinstance(rec, dict) and type(rec.get("seq")) is int and rec["seq"] == n
              and rec.get("prev") == prev and type(rec.get("type")) is str
              and len(line) <= MAX_LINE and _dump(rec) == line
              and all(k in rec for k in _NEEDS.get(rec["type"], ()))
              and (rec["type"] not in ("start", "step", "proposal") or type(rec["id"]) is str))
        if not ok:
            raise LedgerError(f"ledger broken at seq {n}")
        records.append(rec)
        prev = _sha(line)
    return records, prev


def read_path(path: Path) -> tuple[list[dict], bytes]:
    """The records and the bytes read, with every check of `read`. A missing file is an empty ledger."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return [], b""
    except OSError:
        raise LedgerError("cannot read the ledger") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        data = _slurp(fd)
        return _check(data)[0], data
    finally:
        os.close(fd)


def read(cfg: Config) -> list[dict]:
    return read_path(cfg.home / LEDGER)[0]


# shortcut: the last line has no later record to vouch for it, so editing only that line is not
# caught, upgrade by keeping the head hash somewhere else
def _append(cfg: Config, rtype: str, fields: dict) -> dict:
    """Private: only the functions of this module write records. One record, one write. The lock covers the read and the write, so two writers cannot clash."""
    try:
        cfg.home.mkdir(parents=True, exist_ok=True)
        fd = os.open(cfg.home / LEDGER, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise LedgerError("cannot open the ledger") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        records, prev = _check(_slurp(fd))
        seq = len(records) + 1
        rec = {**fields, "seq": seq, "at": now(), "type": rtype, "prev": prev}
        if rtype == "start":
            rec["id"] = f"S-{seq:04d}"
        elif rtype == "proposal":
            rec["id"] = f"P-{1 + sum(r['type'] == 'proposal' for r in records):04d}"
        line = _dump(rec)
        if cfg.model_key and json.dumps(cfg.model_key)[1:-1].encode() in line:
            raise LedgerError("a record holds the model key; refused")
        if len(line) > MAX_LINE:
            raise LedgerError("record is larger than 256 KiB")
        if os.fstat(fd).st_size + len(line) + 1 > MAX_FILE:
            raise LedgerError("ledger is full")
        if os.write(fd, line + b"\n") != len(line) + 1:
            raise LedgerError("the ledger write was cut short")
        return rec
    except OSError:
        raise LedgerError("cannot write the ledger") from None
    finally:
        os.close(fd)


# --- steps and fallbacks (written by the loop) -------------------------------------------------------

def begin_step(cfg: Config, kind: str, text: str, plan: dict) -> str:
    """Written before the tools run, because the id names the run folder. A start with no step
    after it is a run that did not finish."""
    if kind not in ("round", "ask"):
        raise LedgerError("bad step")
    key = "goal" if kind == "round" else "question"
    return _append(cfg, "start", {"kind": kind, key: clean(text, 500), "plan": plan})["id"]


def add_step(cfg: Config, step_id: str, calls: list, conclusion: str, grounding: str | None = None) -> dict:
    """The finished step: what the start said, the tool calls, and the conclusion."""
    records = read(cfg)
    start = next((r for r in records if r["type"] == "start" and r["id"] == step_id), None)
    if start is None or any(r["type"] == "step" and r["id"] == step_id for r in records):
        raise LedgerError(f"no open step {show(step_id)}")
    fields = {k: start[k] for k in ("id", "kind", "goal", "question", "plan") if k in start}
    if grounding is not None:
        fields["grounding"] = grounding
    return _append(cfg, "step", {**fields, "calls": calls, "conclusion": clean(conclusion, 2000)})


def add_fallback(cfg: Config, reason: str, host: str, shown: str = "") -> dict:
    return _append(cfg, "planner_fallback", {"reason": reason, "host": clean(host, 100), "shown": clean(shown, 200)})


def export_path(cfg: Config, path: str) -> Path:
    """Where an export may be written: never inside the models folder."""
    out = Path(path).expanduser().resolve()
    models = cfg.models.resolve()
    if out == models or models in out.parents:
        raise LedgerError("export does not write inside the models folder")
    return out


# --- proposals ----------------------------------------------------------------------------------------

def status(records: list[dict], proposal_id: object) -> str | None:
    """proposed, approved or applied: derived from the records after the proposal. None if unknown."""
    state = None
    for r in records:
        t = r["type"]
        if t == "proposal" and r.get("id") == proposal_id:
            state = "proposed"
        elif state and r.get("proposal") == proposal_id and (t, state) in (
                ("approval", "proposed"), ("applied", "approved")):
            state = "approved" if t == "approval" else "applied"
    return state


def _target(action: str, source: str) -> str:
    """Targets come from this map and the source name, never from text."""
    if action == "adopt_reward_policy" and (m := _POLICY.fullmatch(source)):
        return f"tier_b/reward_policy{m[1] or ''}/{m[2]}"
    if action == "set_baseline_params" and _PARAMS.fullmatch(source):
        return "tier_b/baseline/params.json"
    raise LedgerError(f"{action}: source not allowed: {show(source)!r}")


def _source(cfg: Config, rel: str) -> bytes:
    """A source file's bytes: a regular file inside the agent home, never a symlink."""
    home = cfg.home.resolve()
    path = (home / rel).resolve()
    if not path.is_relative_to(home):
        raise LedgerError(f"{show(rel)}: outside the agent home")
    try:
        fd = os.open(home / rel, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            data = os.read(fd, MAX_SOURCE + 1)
        finally:
            os.close(fd)
    except OSError:
        raise LedgerError(f"{show(rel)}: missing or a symlink") from None
    if len(data) > MAX_SOURCE:
        raise LedgerError(f"{show(rel)}: larger than {MAX_SOURCE} bytes")
    return data


def installed(cfg: Config, rel: str) -> bytes | None:
    """Read only: a file under the models folder, or None when there is none."""
    try:
        fd = os.open(cfg.models / rel, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        raise LedgerError(f"{show(rel)}: cannot be read") from None
    try:
        data = os.read(fd, MAX_SOURCE + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_SOURCE:
        raise LedgerError(f"{show(rel)}: larger than {MAX_SOURCE} bytes")
    return data


def _read_policy(cfg: Config, source: str) -> None:
    try:
        reward_loop.read_policy(cfg.home / source, "/no_temperature/" not in source)
    except TrialError as e:
        raise LedgerError(str(e)) from None


def propose(cfg: Config, action: str, step_id: str, run_dir_rel: str, no_temperature: bool, evidence: dict) -> str:
    """Write a proposal: the files, their hashes, and where they would go. Nothing is copied."""
    if action not in ACTIONS or not _RUN.fullmatch(run_dir_rel) or not isinstance(evidence, dict):
        raise LedgerError("bad proposal")
    records = read(cfg)
    if not any(r["type"] == "step" and r["id"] == step_id for r in records):
        raise LedgerError(f"no step {show(step_id)}")
    if action == "set_baseline_params":
        sources = [f"{run_dir_rel}/params.json"]
    else:
        base = f"{run_dir_rel}/reward_policy" + ("/no_temperature" if no_temperature else "")
        gens = sorted(p.name for p in (cfg.home / base / "generations").glob("gen-[0-9][0-9].json"))
        if len(gens) > MAX_GENERATIONS:
            raise LedgerError(f"more than {MAX_GENERATIONS} generation files")
        sources = [f"{base}/policy.json", *(f"{base}/generations/{g}" for g in gens)]
        _read_policy(cfg, sources[0])
    files = [{"source": s, "sha256": _sha(_source(cfg, s)), "target": _target(action, s)} for s in sources]
    targets = {f["target"] for f in files}
    for r in records:
        if r["type"] == "proposal" and r["action"] == action and status(records, r["id"]) == "proposed" \
                and {f["target"] for f in r["files"]} & targets:
            raise LedgerError(f"{r['id']} already proposes this and is waiting for a person")
    return _append(cfg, "proposal", {"action": action, "step": step_id, "files": files, "evidence": evidence})["id"]


def _os_user() -> str:
    try:
        return clean(getpass.getuser(), 60)
    except (OSError, KeyError):
        return "unknown"


def approve(cfg: Config, proposal_id: str, by: str | None = None) -> dict:
    """Reachable only from the `approve` command: the loop and the planner never import it."""
    user = _os_user()
    by = user if by is None else by
    if type(by) is not str or not _NAME.fullmatch(by):
        raise LedgerError("give --by a name: letters, spaces, . ' - (2 to 60 characters)")
    state = status(read(cfg), proposal_id)
    if state is None:
        raise LedgerError(f"no proposal {show(proposal_id)}")
    if state != "proposed":
        raise LedgerError(f"{proposal_id} is already {state}")
    return _append(cfg, "approval", {"proposal": proposal_id, "by": by, "os_user": user})


def _destination(models, target: str):
    dest = models / target
    if not dest.parent.resolve().is_relative_to(models) or dest.is_symlink() or (dest.exists() and not dest.is_file()):
        raise LedgerError(f"{show(target)}: cannot be written safely")
    return dest


def apply(cfg: Config, proposal_id: str) -> dict:
    """Copy what was approved, byte for byte. Every check runs before the first write."""
    records = read(cfg)
    state = status(records, proposal_id)
    if state != "approved":
        raise LedgerError(f"no proposal {show(proposal_id)}" if state is None
                          else f"{proposal_id} is {state}, not approved" if state == "proposed"
                          else f"{proposal_id} is already applied")
    prop = next(r for r in records if r["type"] == "proposal" and r["id"] == proposal_id)
    models = cfg.models.resolve()
    plan = []
    try:
        if not any(r["type"] == "step" and r["id"] == prop["step"] for r in records):
            raise LedgerError(f"{proposal_id}: its step is not in the ledger")
        if len({f["source"][:11] for f in prop["files"]}) != 1:  # "runs/S-NNNN"
            raise LedgerError(f"{proposal_id}: its files come from more than one run")
        for f in prop["files"]:
            if _target(prop["action"], f["source"]) != f["target"]:
                raise LedgerError(f"{show(f['target'])}: does not match the action")
            data = _source(cfg, f["source"])
            if _sha(data) != f["sha256"]:
                raise LedgerError(f"{show(f['source'])}: changed since it was approved")
            dest = _destination(models, f["target"])
            plan.append((f, data, dest, _sha(dest.read_bytes()) if dest.exists() else None))
        policies = [f["source"] for f in prop["files"] if f["source"].endswith("/policy.json")]
    except (KeyError, TypeError):
        raise LedgerError(f"{proposal_id}: the proposal record is malformed") from None
    if not plan or (prop["action"] == "adopt_reward_policy" and len(policies) != 1):
        raise LedgerError(f"{proposal_id}: the proposal record is malformed")
    if policies:
        _read_policy(cfg, policies[0])
    # The record of what was written must fit: checked now, not after the copy.
    if (cfg.home / LEDGER).stat().st_size + 512 * (len(plan) + 2) > MAX_FILE:
        raise LedgerError("ledger is full; nothing was written")
    done, tmp = [], None
    try:
        for f, data, dest, before in sorted(plan, key=lambda p: p[0]["target"].endswith("policy.json")):
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".tmp")
            with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644), "wb") as out:
                out.write(data)
            os.replace(tmp, dest)
            done.append({"target": f["target"], "sha256_before": before, "sha256_after": _sha(dest.read_bytes())})
    except OSError:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        wrote = f"a file could not be written; {len(done)} of {len(plan)} files were written"
        try:
            _append(cfg, "apply_failed", {"proposal": proposal_id, "files": done})
        except LedgerError as e:
            raise LedgerError(f"{wrote} and that could NOT be recorded ({e})") from None
        raise LedgerError(f"{wrote} and are recorded as apply_failed; run apply again once the cause is "
                          "fixed") from None
    return _append(cfg, "applied", {"proposal": proposal_id, "files": done})

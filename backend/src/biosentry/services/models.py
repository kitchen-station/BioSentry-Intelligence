"""Loads and checks the three model files once (INTEGRATION.md sections 2.1 to 2.3 and 6.3, rulings 1, 13, 14).

`load_models(models_dir)` never raises and never falls back: a refused file gives `NotComputed` with a fixed
reason, one WARNING line at load (the path inside `models_dir` and the fixed reason, never the file's
contents), and the models that do not need that file still load. Only the with-temperature files are read:
the `no_temp` files are never opened, whatever fails. Nothing calls this yet; the load result is fixed for
the life of the process. Research models: synthetic data only, not validated.

What each refused file disables:
  student     -> student AND calibration (q binds one student, so it cannot be checked without it); policy loads.
  calibration -> the calibration only (the student's p still computes, the check does not); policy loads.
  policy      -> the policy only.
  manifest    -> all three (nothing can be verified).
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from biosentry.domain.computed import NotComputed
from biosentry.domain.scoring.tier_b.reward_policy import MAX_BYTES as MAX_POLICY_BYTES
from biosentry.domain.scoring.tier_b.reward_policy import Policy, PolicyError, parse_policy
from biosentry.domain.scoring.tier_c.confidence import MAX_BYTES as MAX_CALIBRATION_BYTES
from biosentry.domain.scoring.tier_c.confidence import Calibration, CalibrationError, binds, parse_calibration
from biosentry.domain.scoring.tier_c.plain_mlp import FORMAT, MAX_BYTES as MAX_STUDENT_BYTES
from biosentry.domain.scoring.tier_c.plain_mlp import Plain, PlainModelError, parse

log = logging.getLogger(__name__)

# Paths inside models_dir. The manifest lists them with a leading "models/" (the repository's folder).
MANIFEST = "manifest.json"
STUDENT = "tier_c/student_vitals/model.json"
CALIBRATION = "tier_c/confidence_check/calibration.json"
CALIBRATION_SIBLING = CALIBRATION + ".sha256"
POLICY = "tier_b/reward_policy/policy.json"
# Size caps. Student, calibration and policy are the readers' caps in the document (real sizes: 34,949, 660 and
# 5,064 bytes). The manifest (6,706 bytes) and the sibling (65 bytes) get room of their own.
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SIBLING_BYTES = 128
STUDENT_NAME = "tier_c/student_vitals"
WIRED_VARIANT = "all"  # ruling 1: with temperature

NO_STUDENT = "student not loaded"
NO_CALIBRATION = "calibration not loaded"
NO_POLICY = "policy not loaded"

T = TypeVar("T")


class _Refused(Exception):
    """One fixed reason, the same words in the log and in the tests."""


@dataclass(frozen=True, slots=True)
class FileProvenance:
    path: str  # inside models_dir
    sha256: str  # of the bytes read, equal to the manifest's
    run_id: str  # the manifest record's run_id


@dataclass(frozen=True, slots=True)
class LoadedModels:
    student: Plain | NotComputed
    calibration: Calibration | NotComputed
    policy: Policy | NotComputed
    student_file: FileProvenance | None
    calibration_file: FileProvenance | None
    policy_file: FileProvenance | None


def _read(root: Path, rel: str, cap: int) -> bytes:
    """The bytes of a plain file at exactly root/rel (no symlink anywhere on the way), at most `cap`."""
    try:
        real = (root / rel).resolve(strict=True)
    except FileNotFoundError:
        raise _Refused("file missing") from None
    except (OSError, RuntimeError):
        raise _Refused("file unreadable") from None
    if real != root.joinpath(*rel.split("/")) or not real.is_file():
        raise _Refused("not a plain file inside the models folder")
    try:
        with real.open("rb") as f:
            data = f.read(cap + 1)
    except OSError:
        raise _Refused("file unreadable") from None
    if len(data) > cap:
        raise _Refused("file over its size cap")
    return data


def _dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    d = dict(pairs)
    if len(d) != len(pairs):
        raise ValueError("duplicate key")
    return d


def _no_constant(name: str) -> None:
    raise ValueError(f"{name} is not allowed")


def _json(raw: bytes) -> Any:
    """Strict JSON: UTF-8, no NaN, Infinity or -Infinity, no duplicate key."""
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_no_constant, object_pairs_hook=_dupes)
    except (ValueError, RecursionError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise _Refused("not valid JSON") from None


def _manifest_records(root: Path) -> list[Any]:
    try:
        records = _json(_read(root, MANIFEST, MAX_MANIFEST_BYTES))["models"]
    except (_Refused, KeyError, TypeError):
        raise _Refused("manifest not usable") from None
    if not isinstance(records, list):
        raise _Refused("manifest not usable")
    return records


def _verified(root: Path, rel: str, cap: int, records: list[Any]) -> tuple[bytes, FileProvenance]:
    """The file's bytes, once the manifest's one record for models/<rel> agrees on size and sha256."""
    data = _read(root, rel, cap)
    found = [r for r in records if isinstance(r, dict) and r.get("path") == f"models/{rel}"]
    if not found:
        raise _Refused("no manifest record")
    if len(found) > 1:
        raise _Refused("two manifest records")
    rec, sha = found[0], hashlib.sha256(data).hexdigest()
    if rec.get("bytes") != len(data) or rec.get("sha256") != sha:
        raise _Refused("bytes differ from the manifest")
    run_id = rec.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise _Refused("manifest record has no run id")
    return data, FileProvenance(rel, sha, run_id)


def _student(data: bytes) -> tuple[Plain, str]:
    d = _json(data)
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise _Refused("wrong format")
    try:
        model = parse(d)
    except (PlainModelError, OverflowError, RecursionError):  # a 400-digit integer overflows isfinite
        raise _Refused("student checks failed") from None
    if model.name != STUDENT_NAME or model.variant != WIRED_VARIANT or model.threshold is None:
        raise _Refused("not the wired student")
    return model, d["source"]["onnx_sha256"]


def _calibration(root: Path, data: bytes, sha: str, model: Plain, onnx_sha256: str) -> Calibration:
    try:
        sibling = _read(root, CALIBRATION_SIBLING, MAX_SIBLING_BYTES)
    except _Refused:
        raise _Refused("checksum sibling unusable") from None
    if sibling.decode("ascii", errors="replace").strip() != sha:
        raise _Refused("checksum sibling differs")
    try:
        cal = parse_calibration(_json(data))
    except (CalibrationError, OverflowError, RecursionError):
        raise _Refused("calibration checks failed") from None
    if not binds(cal, variant=model.variant, onnx_sha256=onnx_sha256, t=model.threshold):
        raise _Refused("calibration does not bind this student")
    return cal


def _policy(data: bytes) -> Policy:
    try:
        return parse_policy(_json(data), temperature=True)
    except (PolicyError, OverflowError, RecursionError):
        raise _Refused("policy checks failed") from None


def _attempt(rel: str, fail: str, make: Callable[[], T]) -> tuple[T | NotComputed, bool]:
    """(the value, True) or (NotComputed(fail), False) with the one WARNING line for this file."""
    try:
        return make(), True
    except _Refused as e:
        log.warning("model file refused: %s: %s", rel, e)
        return NotComputed(fail), False


def load_models(models_dir: Path) -> LoadedModels:
    root = Path(models_dir).resolve()
    try:
        records = _manifest_records(root)
    except _Refused as e:
        for rel in (STUDENT, CALIBRATION, POLICY):
            log.warning("model file refused: %s: %s", rel, e)
        return LoadedModels(NotComputed(NO_STUDENT), NotComputed(NO_STUDENT), NotComputed(NO_POLICY), None, None, None)

    def student() -> tuple[Plain, str, FileProvenance]:
        data, prov = _verified(root, STUDENT, MAX_STUDENT_BYTES, records)
        return *_student(data), prov

    got, ok = _attempt(STUDENT, NO_STUDENT, student)
    if ok:
        model, onnx_sha256, student_prov = got  # type: ignore[misc]

        def calibration() -> tuple[Calibration, FileProvenance]:
            data, prov = _verified(root, CALIBRATION, MAX_CALIBRATION_BYTES, records)
            return _calibration(root, data, prov.sha256, model, onnx_sha256), prov

        cal, cal_ok = _attempt(CALIBRATION, NO_CALIBRATION, calibration)
        cal_value, cal_prov = cal if cal_ok else (cal, None)  # type: ignore[misc]
    else:
        model, student_prov = got, None
        cal_value, cal_prov = NotComputed(NO_STUDENT), None

    def policy() -> tuple[Policy, FileProvenance]:
        data, prov = _verified(root, POLICY, MAX_POLICY_BYTES, records)
        return _policy(data), prov

    pol, pol_ok = _attempt(POLICY, NO_POLICY, policy)
    pol_value, pol_prov = pol if pol_ok else (pol, None)  # type: ignore[misc]
    return LoadedModels(model, cal_value, pol_value, student_prov, cal_prov, pol_prov)

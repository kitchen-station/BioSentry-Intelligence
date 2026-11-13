"""The model loader: what each refused file disables, one WARNING per refused file, nothing raises, no
fall-back to the no-temperature files (INTEGRATION.md sections 2.1 to 2.3, 6.3, 8.5; rulings 1, 13, 14).

Every refusal runs on a copy of the real files in a temporary folder, changed so that only the check under
test can refuse it (the manifest and the checksum sibling are brought into line where the case is not about them).
"""

import hashlib
import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

import biosentry
from biosentry.core.config import DEFAULT_MODELS_DIR, Settings
from biosentry.domain.computed import NotComputed
from biosentry.domain.scoring.tier_b.reward_policy import Policy, digest
from biosentry.domain.scoring.tier_c.confidence import Calibration
from biosentry.domain.scoring.tier_c.plain_mlp import Plain
from biosentry.services import models as m
from biosentry.services.models import (
    CALIBRATION,
    CALIBRATION_SIBLING,
    MANIFEST,
    POLICY,
    STUDENT,
    FileProvenance,
    load_models,
)

REPO = Path(__file__).resolve().parents[3]
REAL = REPO / "models"
NO_TEMP = (
    "tier_c/student_vitals/model_no_temp.json",
    "tier_c/confidence_check/calibration_no_temp.json",
    "tier_c/confidence_check/calibration_no_temp.json.sha256",
    "tier_b/reward_policy/no_temperature/policy.json",
)
WIRED = (MANIFEST, STUDENT, CALIBRATION, CALIBRATION_SIBLING, POLICY)
LOGGER = "biosentry.services.models"


def build(tmp_path: Path) -> Path:
    root = tmp_path / "models"
    for rel in WIRED + NO_TEMP:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REAL / rel, root / rel)
    return root


def manifest_edit(root: Path, fn: Callable[[list], None]) -> None:
    doc = json.loads((root / MANIFEST).read_text("utf-8"))
    fn(doc["models"])
    (root / MANIFEST).write_text(json.dumps(doc), "utf-8")


def put(root: Path, rel: str, data: bytes, *, record: bool = True, sibling: bool = False) -> None:
    (root / rel).unlink(missing_ok=True)
    (root / rel).write_bytes(data)
    if record:
        def fix(recs: list) -> None:
            for r in recs:
                if r["path"] == f"models/{rel}":
                    r["bytes"], r["sha256"] = len(data), hashlib.sha256(data).hexdigest()
        manifest_edit(root, fix)
    if sibling:
        (root / (rel + ".sha256")).write_text(hashlib.sha256(data).hexdigest() + "\n", "ascii")


def edit(root: Path, rel: str, fn: Callable[[dict], None], **kw: bool) -> None:
    obj = json.loads((root / rel).read_text("utf-8"))
    fn(obj)
    put(root, rel, (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode(), **kw)


def drop_record(rel: str) -> Callable[[Path], None]:
    return lambda r: manifest_edit(r, lambda recs: recs.__delitem__(next(
        i for i, x in enumerate(recs) if x["path"] == f"models/{rel}")))


def twice(rel: str) -> Callable[[Path], None]:
    return lambda r: manifest_edit(r, lambda recs: recs.append(next(x for x in recs if x["path"] == f"models/{rel}")))


def flip(rel: str) -> Callable[[Path], None]:
    def go(r: Path) -> None:
        data = bytearray((r / rel).read_bytes())
        data[-3] ^= 1
        (r / rel).write_bytes(bytes(data))  # the manifest keeps the old sha256
    return go


def remove(rel: str) -> Callable[[Path], None]:
    return lambda r: (r / rel).unlink()


def directory(rel: str) -> Callable[[Path], None]:
    return lambda r: ((r / rel).unlink(), (r / rel).mkdir())


def outside(rel: str) -> Callable[[Path], None]:
    def go(r: Path) -> None:
        target = r.parent / "outside.json"
        shutil.copyfile(r / rel, target)  # the same bytes, so only the escape can refuse it
        (r / rel).unlink()
        (r / rel).symlink_to(target)
    return go


def loop(rel: str) -> Callable[[Path], None]:
    return lambda r: ((r / rel).unlink(), (r / rel).symlink_to(r / rel))


def big(rel: str, cap: int) -> Callable[[Path], None]:
    return lambda r: put(r, rel, b" " * (cap + 1), record=rel != MANIFEST)


def raw(rel: str, data: bytes) -> Callable[[Path], None]:
    return lambda r: put(r, rel, data, record=rel != MANIFEST, sibling=rel == CALIBRATION)


def text_swap(rel: str, old: str, new: str) -> Callable[[Path], None]:
    def go(r: Path) -> None:
        text = (r / rel).read_text("utf-8")
        assert old in text
        put(r, rel, text.replace(old, new).encode(), sibling=rel == CALIBRATION)
    return go


def change(rel: str, fn: Callable[[dict], None], sibling: bool = False) -> Callable[[Path], None]:
    return lambda r: edit(r, rel, fn, sibling=sibling)


def resealed_policy(fn: Callable[[dict], None]) -> Callable[[Path], None]:
    def go(r: Path) -> None:
        def body(o: dict) -> None:
            fn(o)
            o.pop("sha256")
            o["sha256"] = digest(o)
        edit(r, POLICY, body)
    return go


S, C, P = "student", "calibration", "policy"
OTHER_STUDENT = lambda r: put(r, STUDENT, (REAL / NO_TEMP[0]).read_bytes())  # noqa: E731
OTHER_POLICY = lambda r: put(r, POLICY, (REAL / NO_TEMP[3]).read_bytes())  # noqa: E731

# name: (how to break it, the file that is refused, the fixed reason in the log)
CASES = {
    "student missing": (remove(STUDENT), S, "file missing"),
    "student is a directory": (directory(STUDENT), S, "not a plain file inside the models folder"),
    "student symlink outside": (outside(STUDENT), S, "not a plain file inside the models folder"),
    "student symlink loop": (loop(STUDENT), S, "file unreadable"),
    "student over cap": (big(STUDENT, m.MAX_STUDENT_BYTES), S, "file over its size cap"),
    "student one changed byte": (flip(STUDENT), S, "bytes differ from the manifest"),
    "student no record": (drop_record(STUDENT), S, "no manifest record"),
    "student two records": (twice(STUDENT), S, "two manifest records"),
    "student record size": (lambda r: manifest_edit(r, lambda recs: [
        x.update(bytes=1) for x in recs if x["path"] == f"models/{STUDENT}"]), S, "bytes differ from the manifest"),
    "student record without run id": (lambda r: manifest_edit(r, lambda recs: [
        x.pop("run_id") for x in recs if x["path"] == f"models/{STUDENT}"]), S, "manifest record has no run id"),
    "student wrong format": (change(STUDENT, lambda o: o.update(format="x")), S, "wrong format"),
    "student not an object": (raw(STUDENT, b"[]"), S, "wrong format"),
    "student NaN": (text_swap(STUDENT, '"threshold": 0.006', '"threshold": NaN'), S, "not valid JSON"),
    "student Infinity": (text_swap(STUDENT, '"threshold": 0.006', '"threshold": -Infinity'), S, "not valid JSON"),
    "student huge number": (text_swap(STUDENT, '"threshold": 0.006', '"threshold": 1e999'), S, "student checks failed"),
    "student duplicate key": (raw(STUDENT, b'{"format": "a", "format": "b"}'), S, "not valid JSON"),
    "student not UTF-8": (raw(STUDENT, b"\xff\xfe"), S, "not valid JSON"),
    "student zero std": (change(STUDENT, lambda o: o["std"].__setitem__(0, 0.0)), S, "student checks failed"),
    "student other variant": (OTHER_STUDENT, S, "not the wired student"),
    "student no threshold": (change(STUDENT, lambda o: o.update(threshold=None)), S, "not the wired student"),
    "calibration missing": (remove(CALIBRATION), C, "file missing"),
    "calibration over cap": (big(CALIBRATION, m.MAX_CALIBRATION_BYTES), C, "file over its size cap"),
    "calibration one changed byte": (flip(CALIBRATION), C, "bytes differ from the manifest"),
    "calibration no record": (drop_record(CALIBRATION), C, "no manifest record"),
    "calibration two records": (twice(CALIBRATION), C, "two manifest records"),
    "calibration sibling differs": (lambda r: (r / CALIBRATION_SIBLING).write_text("0" * 64 + "\n"), C,
                                    "checksum sibling differs"),
    "calibration sibling empty": (lambda r: (r / CALIBRATION_SIBLING).write_text(""), C, "checksum sibling differs"),
    "calibration sibling missing": (remove(CALIBRATION_SIBLING), C, "checksum sibling unusable"),
    "calibration sibling over cap": (lambda r: (r / CALIBRATION_SIBLING).write_bytes(b"0" * 200), C,
                                     "checksum sibling unusable"),
    "calibration for the other variant": (change(CALIBRATION, lambda o: o.update(variant="no_temp"), True), C,
                                          "calibration does not bind this student"),
    "calibration bound to another model": (change(CALIBRATION, lambda o: o.update(onnx_sha256="0" * 64), True), C,
                                           "calibration does not bind this student"),
    "calibration other threshold": (change(CALIBRATION, lambda o: o.update(t=0.007), True), C,
                                    "calibration does not bind this student"),
    "calibration other alpha": (change(CALIBRATION, lambda o: o.update(alpha="0.05"), True), C,
                                "calibration checks failed"),
    "calibration unknown key": (change(CALIBRATION, lambda o: o.update(extra=1), True), C,
                                "calibration checks failed"),
    "calibration NaN": (text_swap(CALIBRATION, '"t": 0.006', '"t": NaN'), C, "not valid JSON"),
    "policy missing": (remove(POLICY), P, "file missing"),
    "policy over cap": (big(POLICY, m.MAX_POLICY_BYTES), P, "file over its size cap"),
    "policy one changed byte": (flip(POLICY), P, "bytes differ from the manifest"),
    "policy no record": (drop_record(POLICY), P, "no manifest record"),
    "policy two records": (twice(POLICY), P, "two manifest records"),
    "policy changed weight, old content sha256": (
        change(POLICY, lambda o: o["params"]["weights"].update(heart_rate=9.0)), P, "policy checks failed"),
    "policy wrong temperature flag": (resealed_policy(lambda o: o.update(temperature=False)), P,
                                      "policy checks failed"),
    "policy other file": (OTHER_POLICY, P, "policy checks failed"),
    "policy NaN": (text_swap(POLICY, '"theta": 2.9776', '"theta": NaN'), P, "not valid JSON"),
}

DISABLED = {
    S: ("student", "calibration"),  # q binds one student: without it the calibration cannot be checked
    C: ("calibration",),
    P: ("policy",),
}
FIELDS = {"student": ("student", "student not loaded"), "calibration": ("calibration", "calibration not loaded"),
          "policy": ("policy", "policy not loaded")}


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def attempt(root: Path, caplog: pytest.LogCaptureFixture) -> tuple[m.LoadedModels, list[str]]:
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        loaded = load_models(root)
    return loaded, warnings(caplog)


def check_case(loaded: m.LoadedModels, refused: str, lines: list[str], rel: str, reason: str) -> None:
    assert lines == [f"model file refused: {rel}: {reason}"]  # exactly one, the path and the fixed words
    for which in DISABLED[refused]:
        attr, words = FIELDS[which]
        expected = "student not loaded" if refused == S and which == "calibration" else words
        assert getattr(loaded, attr) == NotComputed(expected)
        assert getattr(loaded, attr + "_file") is None
    for which in {"student", "calibration", "policy"} - set(DISABLED[refused]):
        assert not isinstance(getattr(loaded, which), NotComputed)
        assert getattr(loaded, which + "_file") is not None


@pytest.mark.parametrize("case", CASES)
def test_a_refused_file_disables_only_what_needs_it(case: str, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    how, refused, reason = CASES[case]
    root = build(tmp_path)
    how(root)
    loaded, lines = attempt(root, caplog)
    rel = {S: STUDENT, C: CALIBRATION, P: POLICY}[refused]
    check_case(loaded, refused, lines, rel, reason)


def test_an_unreadable_file_is_refused(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    root = build(tmp_path)
    real_open = Path.open

    def deny(self: Path, *a: object, **k: object):
        if self.name == "model.json":
            raise PermissionError
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", deny)
    loaded, lines = attempt(root, caplog)
    check_case(loaded, S, lines, STUDENT, "file unreadable")


MANIFEST_CASES = {
    "missing": remove(MANIFEST),
    "not JSON": raw(MANIFEST, b"{"),
    "not an object": raw(MANIFEST, b"[]"),
    "no models key": raw(MANIFEST, b"{}"),
    "models not a list": raw(MANIFEST, b'{"models": 5}'),
    "over cap": big(MANIFEST, m.MAX_MANIFEST_BYTES),
    "a symlink": outside(MANIFEST),
}


@pytest.mark.parametrize("case", MANIFEST_CASES)
def test_an_unusable_manifest_disables_all_three(case: str, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = build(tmp_path)
    MANIFEST_CASES[case](root)
    loaded, lines = attempt(root, caplog)
    assert lines == [f"model file refused: {rel}: manifest not usable" for rel in (STUDENT, CALIBRATION, POLICY)]
    assert loaded.student == NotComputed("student not loaded") and loaded.calibration == NotComputed("student not loaded")
    assert loaded.policy == NotComputed("policy not loaded")
    assert (loaded.student_file, loaded.calibration_file, loaded.policy_file) == (None, None, None)


def test_a_missing_folder_does_not_raise(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    loaded, lines = attempt(tmp_path / "nowhere", caplog)
    assert len(lines) == 3 and isinstance(loaded.policy, NotComputed)


def test_every_file_missing_gives_one_line_for_each_file_that_was_read(tmp_path: Path,
                                                                       caplog: pytest.LogCaptureFixture) -> None:
    root = build(tmp_path)
    for rel in (STUDENT, CALIBRATION, POLICY):
        remove(rel)(root)
    loaded, lines = attempt(root, caplog)
    # the calibration is not read once the student is refused, so it logs nothing of its own
    assert lines == [f"model file refused: {rel}: file missing" for rel in (STUDENT, POLICY)]
    assert loaded.student == NotComputed("student not loaded") and loaded.policy == NotComputed("policy not loaded")


def test_a_bad_calibration_does_not_switch_to_the_no_temperature_files(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build(tmp_path)
    remove(CALIBRATION)(root)
    opened: list[str] = []
    real_open = Path.open

    def spy(self: Path, *a: object, **k: object):
        opened.append(str(self.relative_to(root)))
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", spy)
    loaded, lines = attempt(root, caplog)
    assert loaded.calibration == NotComputed("calibration not loaded") and len(lines) == 1
    assert opened and not [o for o in opened if "no_temp" in o]


def test_the_real_models_folder_loads_all_three_with_the_manifests_provenance(caplog: pytest.LogCaptureFixture) -> None:
    opened: list[str] = []
    real_open = Path.open
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "open", lambda self, *a, **k: (opened.append(self.name), real_open(self, *a, **k))[1])
        loaded, lines = attempt(REAL, caplog)
    assert lines == []
    assert sorted(opened) == sorted(Path(r).name for r in (MANIFEST, STUDENT, CALIBRATION, CALIBRATION_SIBLING, POLICY))
    assert isinstance(loaded.student, Plain) and loaded.student.threshold == 0.006
    assert isinstance(loaded.calibration, Calibration) and loaded.calibration.q == 0.019854247570037842
    assert isinstance(loaded.policy, Policy) and (loaded.policy.params.p, loaded.policy.params.theta) == (5, 2.9776)
    records = {r["path"]: r for r in json.loads((REAL / MANIFEST).read_text("utf-8"))["models"]}
    for rel, got, run_id in (
        (STUDENT, loaded.student_file, "m1-20261113-3d634f3"),
        (CALIBRATION, loaded.calibration_file, "m3-20261113-fbd39f2"),
        (POLICY, loaded.policy_file, "m2-20261113"),
    ):
        rec = records[f"models/{rel}"]
        assert got == FileProvenance(rel, rec["sha256"], rec["run_id"]) and rec["run_id"] == run_id
        assert got.sha256 == hashlib.sha256((REAL / rel).read_bytes()).hexdigest()


def test_the_paths_in_the_log_are_the_fixed_ones_and_never_a_content(tmp_path: Path,
                                                                     caplog: pytest.LogCaptureFixture) -> None:
    root = build(tmp_path)
    put(root, STUDENT, b'{"secret-patient-value": 1}', record=True)
    _, lines = attempt(root, caplog)
    assert len(lines) == 1 and "secret" not in lines[0] and str(tmp_path) not in lines[0]


def test_the_default_models_folder_is_the_repositorys_and_the_environment_overrides_it(tmp_path: Path) -> None:
    assert DEFAULT_MODELS_DIR == Path(biosentry.__file__).resolve().parents[3] / "models" == REAL
    assert (DEFAULT_MODELS_DIR / MANIFEST).is_file()
    assert Settings.from_env({}).models_dir == REAL
    assert Settings.from_env({"BIOSENTRY_MODELS_DIR": "  "}).models_dir == REAL
    assert Settings.from_env({"BIOSENTRY_MODELS_DIR": f" {tmp_path} "}).models_dir == tmp_path

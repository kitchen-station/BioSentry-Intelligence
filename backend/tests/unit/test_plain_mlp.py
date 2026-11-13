"""The student's forward pass: a byte-for-byte copy of research/plain_student.py, the float32 step, and parity
with the research code on a committed batch (INTEGRATION.md sections 4.6 and 4.8, ruling 9).

The fixture data/plain_mlp_parity.json was made with the standard library only, from the repository root:

    python3 - > backend/tests/unit/data/plain_mlp_parity.json <<'PY'
    import hashlib, importlib.util, json, random, struct, sys
    spec = importlib.util.spec_from_file_location("plain_student", "research/plain_student.py")
    m = importlib.util.module_from_spec(spec); sys.modules["plain_student"] = m; spec.loader.exec_module(m)
    f32 = lambda x: struct.unpack("f", struct.pack("f", x))[0]
    out = {"about": "research.plain_student.forward on 100 float32-rounded rows per student file, seed 7"}
    for name in ("model.json", "model_no_temp.json"):
        path = "models/tier_c/student_vitals/" + name
        model, rng, rows = m.load(path), random.Random(7), []
        for i in range(100):
            row = [mean + rng.uniform(-4, 4) * std for mean, std in zip(model.mean, model.std)]
            if i >= 80:  # the last rows mix in flags, as the research batch does
                row = [float(rng.randrange(2)) if rng.random() < 0.5 else x for x in row]
            rows.append([f32(x) for x in row])
        out[name] = {"model_sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
                     "rows": rows, "p": [m.forward(model, r) for r in rows]}
    json.dump(out, sys.stdout, separators=(",", ":")); print()
    PY

This test never imports research/. It only reads research/plain_student.py as text to compare it with the copy.
"""

import copy
import hashlib
import json
import math
import re
from pathlib import Path

import pytest

from biosentry.domain.scoring.tier_c.float32 import to_float32
from biosentry.domain.scoring.tier_c.plain_mlp import (
    CLAMP,
    MAX_BYTES,
    Plain,
    PlainModelError,
    canonical_bytes,
    forward,
    load,
    parse,
)

REPO = Path(__file__).resolve().parents[3]
STUDENTS = REPO / "models" / "tier_c" / "student_vitals"
COPY = REPO / "backend" / "src" / "biosentry" / "domain" / "scoring" / "tier_c" / "plain_mlp.py"
RESEARCH = REPO / "research" / "plain_student.py"
FIXTURE = json.loads((Path(__file__).parent / "data" / "plain_mlp_parity.json").read_text("utf-8"))
NAMES = ("model.json", "model_no_temp.json")
GOOD = json.loads((STUDENTS / "model.json").read_text("utf-8"))


def test_the_copy_is_the_research_file_under_a_two_line_header() -> None:
    mine, research = COPY.read_text("utf-8"), RESEARCH.read_text("utf-8")
    assert len(research) > 1000  # not vacuous
    assert mine.endswith(research)
    first, second = mine[: -len(research)].splitlines()
    assert re.fullmatch(
        r"# Copied verbatim from research/plain_student\.py in kitchen-station/BioSentry-Intelligence"
        r" at commit [0-9a-f]{40}\.",
        first,
    )
    assert second == (
        "# Do not edit here: change the research file and copy again. Research code: synthetic data only,"
        " not validated."
    )


@pytest.mark.parametrize("name", NAMES)
def test_the_forward_pass_equals_the_research_values_on_the_committed_batch(name: str) -> None:
    fx = FIXTURE[name]
    assert fx["model_sha256"] == hashlib.sha256((STUDENTS / name).read_bytes()).hexdigest()
    model = load(STUDENTS / name)
    assert len(fx["rows"]) == len(fx["p"]) == 100
    assert all(len(r) == len(model.inputs) for r in fx["rows"])
    assert max(abs(forward(model, r) - p) for r, p in zip(fx["rows"], fx["p"])) <= 1e-12
    assert len({p for p in fx["p"]}) > 50  # the batch is not constant


@pytest.mark.parametrize("name", NAMES)
def test_rounding_unrounded_doubles_gives_the_same_p(name: str) -> None:
    fx, model = FIXTURE[name], load(STUDENTS / name)
    nudged = [[x * (1 + 1e-9) for x in r] for r in fx["rows"]]  # near a float32 value, not on it
    assert any(x != y for r, n in zip(fx["rows"], nudged) for x, y in zip(r, n))
    for row, near, p in zip(fx["rows"], nudged, fx["p"]):
        assert [to_float32(x) for x in near] == row
        assert abs(forward(model, [to_float32(x) for x in near]) - p) <= 1e-12


def test_float32_rounding_and_its_overflow() -> None:
    assert to_float32(0.1) == 0.10000000149011612
    assert to_float32(0.5) == 0.5
    assert math.isnan(to_float32(math.nan)) and to_float32(math.inf) == math.inf
    for beyond in (1e39, -1e39, 3.4028236e38, 1e300):
        with pytest.raises(OverflowError):
            to_float32(beyond)
    assert to_float32(3.4028235e38) == 3.4028234663852886e38
    for not_a_number in (True, False, "1", None, [1.0]):
        with pytest.raises(TypeError):
            to_float32(not_a_number)  # type: ignore[arg-type]
    assert to_float32(2) == 2.0


def test_the_wired_student_has_the_documented_threshold_and_shape() -> None:
    model = load(STUDENTS / "model.json")
    assert (model.threshold, model.variant, len(model.inputs)) == (0.006, "all", 35)
    assert load(STUDENTS / "model_no_temp.json").threshold == 0.006


def row_of(model: Plain) -> list[float]:
    return [m for m in model.mean]


@pytest.mark.parametrize("which", ["short", "long", "empty", "nan", "inf", "-inf", "bool", "string", "none"])
def test_a_bad_row_is_refused(which: str) -> None:
    model = load(STUDENTS / "model.json")
    row = row_of(model)
    bad = {"short": row[:-1], "long": row + [0.0], "empty": []}.get(which)
    if bad is None:
        row[3] = {"nan": math.nan, "inf": math.inf, "-inf": -math.inf, "bool": True, "string": "1.0", "none": None}[which]
        bad = row
    with pytest.raises(PlainModelError):
        forward(model, bad)


@pytest.mark.parametrize("row", ["a" * 35, b"a" * 35])
def test_a_string_or_bytes_row_is_refused(row: str | bytes) -> None:
    with pytest.raises(PlainModelError, match="exactly 35"):
        forward(load(STUDENTS / "model.json"), row)


def tiny(bias: float) -> Plain:
    return Plain("t", "all", ("x",), (0.0,), (1.0,), ((((1.0,),), (0.0,)), (((1.0,),), (bias,))), 0.5)


def test_the_sigmoid_both_sides_and_the_clamp() -> None:
    assert forward(tiny(0.0), [2]) == pytest.approx(1 / (1 + math.exp(-2)))  # z >= 0
    assert forward(tiny(0.0), [-5]) == 0.5  # relu gives 0, z == 0
    assert forward(tiny(-1.0), [0]) == pytest.approx(math.exp(-1) / (1 + math.exp(-1)))  # z < 0
    assert forward(tiny(0.0), [1e300]) == pytest.approx(1 / (1 + math.exp(-CLAMP)))  # clamped, still finite


def test_canonical_bytes_are_the_files_bytes() -> None:
    assert canonical_bytes(GOOD) == (STUDENTS / "model.json").read_bytes()


def broken(mutate) -> dict:
    d = copy.deepcopy(GOOD)
    mutate(d)
    return d


def set_(key: str, value) -> object:
    return lambda d: d.__setitem__(key, value)


REFUSED = {
    "format": set_("format", "x"),
    "extra key": set_("extra", 1),
    "missing key": lambda d: d.pop("threshold"),
    "activation": set_("hidden_activation", "tanh"),
    "output": set_("output", "softmax"),
    "synthetic type": set_("synthetic", "yes"),
    "validated type": set_("validated", 1),
    "source not a dict": set_("source", []),
    "source key": lambda d: d["source"].pop("framework"),
    "source value": lambda d: d["source"].__setitem__("onnx_sha256", 5),
    "inputs empty": set_("inputs", []),
    "inputs not a list": set_("inputs", "abc"),
    "inputs entry": lambda d: d["inputs"].__setitem__(0, 7),
    "inputs duplicate": lambda d: d["inputs"].__setitem__(1, d["inputs"][0]),
    "mean length": lambda d: d["mean"].pop(),
    "mean not a list": set_("mean", 1.0),
    "mean infinite": lambda d: d["mean"].__setitem__(0, 1e999),
    "mean bool": lambda d: d["mean"].__setitem__(0, True),
    "std zero": lambda d: d["std"].__setitem__(0, 0.0),
    "layers not a list": set_("layers", {}),
    "three layers": lambda d: d["layers"].append(d["layers"][0]),
    "layer not a dict": lambda d: d["layers"].__setitem__(0, []),
    "layer keys": lambda d: d["layers"][0].pop("bias"),
    "bias not a list": lambda d: d["layers"][0].__setitem__("bias", 1.0),
    "bias empty": lambda d: d["layers"][0].__setitem__("bias", []),
    "output layer width": lambda d: d["layers"][1].__setitem__("bias", [0.0, 0.0]),
    "weights rows": lambda d: d["layers"][0]["weights"].pop(),
    "weights not a list": lambda d: d["layers"][0].__setitem__("weights", "x"),
    "weight row length": lambda d: d["layers"][0]["weights"][0].pop(),
    "threshold high": set_("threshold", 1.5),
    "threshold low": set_("threshold", -0.1),
    "threshold type": set_("threshold", "x"),
    "name empty": set_("name", ""),
}


@pytest.mark.parametrize("case", REFUSED)
def test_a_malformed_student_is_refused(case: str) -> None:
    with pytest.raises(PlainModelError):
        parse(broken(REFUSED[case]))


def test_a_student_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(PlainModelError):
        parse([])


def test_a_student_with_no_threshold_parses() -> None:
    assert parse(broken(set_("threshold", None))).threshold is None


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_load_refuses_a_big_file_bad_json_a_nan_and_deep_nesting(tmp_path: Path) -> None:
    with pytest.raises(PlainModelError, match="larger than"):
        load(write(tmp_path / "big", b" " * (MAX_BYTES + 1)))
    for data in (b"not json", b"\xff\xfe", b"[" * 200000, b'{"format": NaN}', b'{"format": Infinity}'):
        with pytest.raises(PlainModelError):
            load(write(tmp_path / "x", data))
    assert load(STUDENTS / "model.json").name == "tier_c/student_vitals"

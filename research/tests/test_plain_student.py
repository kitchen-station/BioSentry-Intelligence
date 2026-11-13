import copy
import json
import math

import pytest

from research import plain_student as ps
from research.plain_student import PlainModelError, canonical_bytes, forward, load

# 2 inputs -> 2 hidden units -> 1 output; weights[unit][input]
MODEL = {
    "format": "biosentry-plain-mlp-1", "name": "toy", "variant": "all", "inputs": ["a", "b"],
    "mean": [1.0, 2.0], "std": [2.0, 4.0],
    "layers": [{"weights": [[1.0, 0.0], [0.5, -0.5]], "bias": [0.0, 0.25]},
               {"weights": [[1.0, 2.0]], "bias": [-0.5]}],
    "hidden_activation": "relu", "output": "sigmoid", "threshold": 0.25,
    "source": {"framework": "MindSpore", "framework_version": "x", "onnx_file": "model.onnx", "onnx_sha256": "0" * 64},
    "synthetic": True, "validated": False,
}


def write(tmp_path, d, name="m.json"):
    path = tmp_path / name
    path.write_bytes(canonical_bytes(d))
    return path


def sig(z):
    return 1 / (1 + math.exp(-z))


def test_forward_matches_hand_computed_rows(tmp_path):
    m = load(write(tmp_path, MODEL))
    assert m.threshold == 0.25 and m.inputs == ("a", "b")
    # [3, 6]: z = (1, 1); hidden = (1, 0.25); out = 1 + 0.5 - 0.5 = 1
    assert forward(m, [3, 6]) == pytest.approx(sig(1.0), abs=1e-15)
    # [1, 2]: z = (0, 0); hidden = (0, 0.25); out = 0.5 - 0.5 = 0
    assert forward(m, [1.0, 2.0]) == 0.5
    # [-1, 10]: z = (-1, 2); hidden pre-ReLU = (-1, -1.25), both cut to 0; out = -0.5
    assert forward(m, [-1.0, 10.0]) == pytest.approx(sig(-0.5), abs=1e-15)
    # [5, 2]: z = (2, 0); hidden = (2, 1.25); out = 2 + 2.5 - 0.5 = 4
    assert forward(m, (5.0, 2.0)) == pytest.approx(sig(4.0), abs=1e-15)


@pytest.mark.parametrize("x", [[1e300, -1e300], [-1e300, 1e300], [3.4e38, 3.4e38], [0.0, 0.0]])
def test_extreme_inputs_give_a_finite_probability(tmp_path, x):
    p = forward(load(write(tmp_path, MODEL)), x)
    assert math.isfinite(p) and 0.0 <= p <= 1.0


def test_extreme_weights_still_give_a_probability(tmp_path):
    d = copy.deepcopy(MODEL)
    d["layers"][0]["weights"] = [[3e38, 3e38], [-3e38, 3e38]]
    d["layers"][1]["weights"] = [[3e38, 3e38]]
    for x in ([1e300, 1e300], [-1e300, -1e300]):
        assert 0.0 <= forward(load(write(tmp_path, d)), x) <= 1.0


@pytest.mark.parametrize("row", [[1.0], [1.0, 2.0, 3.0], [], "ab", [float("nan"), 1.0], [1.0, float("inf")],
                                 [None, 1.0], ["1", 2.0], [True, 2.0]])
def test_a_wrong_length_or_unusable_row_is_refused(tmp_path, row):
    with pytest.raises(PlainModelError):
        forward(load(write(tmp_path, MODEL)), row)


def mutate(f):
    d = copy.deepcopy(MODEL)
    f(d)
    return d


BAD = {
    "unknown version": mutate(lambda d: d.update(format="biosentry-plain-mlp-2")),
    "extra key": mutate(lambda d: d.update(extra=1)),
    "missing key": mutate(lambda d: d.pop("threshold")),
    "wrong activation": mutate(lambda d: d.update(hidden_activation="tanh")),
    "std of zero": mutate(lambda d: d["std"].__setitem__(1, 0.0)),
    "negative std": mutate(lambda d: d["std"].__setitem__(1, -1.0)),
    "inputs too short": mutate(lambda d: d.update(inputs=["a"])),
    "duplicate inputs": mutate(lambda d: d.update(inputs=["a", "a"])),
    "mean too long": mutate(lambda d: d.update(mean=[1.0, 2.0, 3.0])),
    "weights wrong shape": mutate(lambda d: d["layers"][0]["weights"].__setitem__(0, [1.0])),
    "weights transposed": mutate(lambda d: d["layers"][0].update(weights=[[1.0, 0.5], [0.0, -0.5]], bias=[0.0, 0.25, 0.0])),
    "output not one unit": mutate(lambda d: d["layers"][1].update(weights=[[1.0, 2.0], [1.0, 2.0]], bias=[0.0, 0.0])),
    "three layers": mutate(lambda d: d["layers"].append(d["layers"][1])),
    "bool as number": mutate(lambda d: d["mean"].__setitem__(0, True)),
    "string as number": mutate(lambda d: d["layers"][1]["bias"].__setitem__(0, "1")),
    "null as number": mutate(lambda d: d["layers"][0]["weights"][0].__setitem__(0, None)),
    "threshold a string": mutate(lambda d: d.update(threshold="0.5")),
    "threshold above 1": mutate(lambda d: d.update(threshold=1.5)),
    "validated not bool": mutate(lambda d: d.update(validated=0)),
    "source incomplete": mutate(lambda d: d["source"].pop("onnx_sha256")),
}


@pytest.mark.parametrize("name", list(BAD))
def test_the_reader_refuses(tmp_path, name):
    with pytest.raises(PlainModelError):
        load(write(tmp_path, BAD[name]))


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_non_finite_numbers_are_refused(tmp_path, text):
    path = tmp_path / "m.json"
    path.write_text(canonical_bytes(MODEL).decode().replace('"mean": [\n  1.0', '"mean": [\n  ' + text, 1))
    with pytest.raises(PlainModelError):
        load(path)


def test_not_json_and_too_large_are_refused(tmp_path):
    (tmp_path / "a.json").write_bytes(b"\xff\xfe")
    (tmp_path / "b.json").write_text("[" * 20000)
    (tmp_path / "c.json").write_bytes(b" " * (ps.MAX_BYTES + 1))
    for n in "abc":
        with pytest.raises(PlainModelError):
            load(tmp_path / f"{n}.json")


def test_a_null_threshold_is_accepted(tmp_path):
    assert load(write(tmp_path, mutate(lambda d: d.update(threshold=None)))).threshold is None


def test_canonical_bytes_round_trip_and_shape(tmp_path):
    data = canonical_bytes(MODEL)
    assert data.endswith(b"}\n") and json.loads(data) == MODEL
    assert canonical_bytes(json.loads(data)) == data
    assert data == (json.dumps(MODEL, sort_keys=True, indent=1) + "\n").encode()
    assert data.index(b'"format"') < data.index(b'"inputs"') < data.index(b'"layers"')  # sorted keys
    assert load(write(tmp_path, MODEL)).std == (2.0, 4.0)

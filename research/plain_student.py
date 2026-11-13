"""The student as a plain JSON file, and a forward pass that needs nothing but the standard library (slice M5).

Format "biosentry-plain-mlp-1": mean and std hold one number per input; each layer holds "weights"
and "bias". weights[unit][input]: the OUTER index is the output unit, the INNER index is the input
(the transpose of the ONNX initialiser, which is [input][unit]). Layer 0 is n inputs -> h units
(ReLU), layer 1 is h units -> 1 unit (sigmoid). Every number is the float32 value written with
Python's repr, so it reads back exactly. Research only; synthetic, not validated.
"""

import json
import math
from dataclasses import dataclass

FORMAT = "biosentry-plain-mlp-1"
MAX_BYTES = 1 << 20
# shortcut: a standardised input beyond 1e6 sd is clamped so the sum stays finite; a real row is
# within a few hundred sd. Revisit only if a model is ever trained on features with such a spread.
CLAMP = 1e6
_KEYS = {"format", "name", "variant", "inputs", "mean", "std", "layers", "hidden_activation", "output",
         "threshold", "source", "synthetic", "validated"}
_SOURCE = {"framework", "framework_version", "onnx_file", "onnx_sha256"}


class PlainModelError(ValueError):
    pass


@dataclass(frozen=True)
class Plain:
    name: str
    variant: str
    inputs: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    layers: tuple[tuple[tuple[tuple[float, ...], ...], tuple[float, ...]], ...]  # ((weights[unit][input], bias),)
    threshold: float | None


def canonical_bytes(model_dict: dict) -> bytes:
    return (json.dumps(model_dict, sort_keys=True, indent=1, allow_nan=False) + "\n").encode()


def _num(v, what: str) -> float:
    if type(v) not in (int, float) or not math.isfinite(v):
        raise PlainModelError(f"{what} is not a finite number")
    return float(v)


def _nums(v, n: int, what: str) -> tuple[float, ...]:
    if not isinstance(v, list) or len(v) != n:
        raise PlainModelError(f"{what} must be a list of {n} numbers")
    return tuple(_num(x, what) for x in v)


def _str(v, what: str) -> str:
    if not isinstance(v, str) or not v:
        raise PlainModelError(f"{what} must be a non-empty string")
    return v


def parse(d) -> Plain:
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise PlainModelError(f"format must be {FORMAT!r}")
    if set(d) != _KEYS:
        raise PlainModelError("unexpected or missing keys")
    if d["hidden_activation"] != "relu" or d["output"] != "sigmoid":
        raise PlainModelError("only relu then sigmoid is supported")
    if type(d["synthetic"]) is not bool or type(d["validated"]) is not bool:
        raise PlainModelError("synthetic and validated must be true or false")
    src = d["source"]
    if not isinstance(src, dict) or set(src) != _SOURCE or not all(isinstance(v, str) for v in src.values()):
        raise PlainModelError("source must hold four strings")
    inputs = d["inputs"]
    if not isinstance(inputs, list) or not inputs or not all(isinstance(s, str) and s for s in inputs) \
            or len(set(inputs)) != len(inputs):
        raise PlainModelError("inputs must be a list of distinct non-empty names")
    n = len(inputs)
    mean, std = _nums(d["mean"], n, "mean"), _nums(d["std"], n, "std")
    if min(std) <= 0:
        raise PlainModelError("std must be above 0")
    layers = d["layers"]
    if not isinstance(layers, list) or len(layers) != 2:
        raise PlainModelError("exactly two layers are expected")
    parsed, width = [], n
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict) or set(layer) != {"weights", "bias"}:
            raise PlainModelError(f"layer {i}: needs weights and bias")
        w, b = layer["weights"], layer["bias"]
        if not isinstance(b, list) or not b or (i == 1 and len(b) != 1):
            raise PlainModelError(f"layer {i}: bias has the wrong length")
        if not isinstance(w, list) or len(w) != len(b):
            raise PlainModelError(f"layer {i}: weights must have one row per unit")
        parsed.append((tuple(_nums(row, width, f"layer {i} weights") for row in w), _nums(b, len(b), f"layer {i} bias")))
        width = len(b)
    t = d["threshold"]
    if t is not None:
        t = _num(t, "threshold")
        if not 0 <= t <= 1:
            raise PlainModelError("threshold must be in [0, 1]")
    return Plain(_str(d["name"], "name"), _str(d["variant"], "variant"), tuple(inputs), mean, std, tuple(parsed), t)


def _no_constant(name: str):
    raise PlainModelError(f"{name} is not allowed in the file")


def load(path) -> Plain:
    with open(path, "rb") as f:
        raw = f.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise PlainModelError(f"file is larger than {MAX_BYTES} bytes")
    try:
        d = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, RecursionError) as e:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        if isinstance(e, PlainModelError):
            raise
        raise PlainModelError("not valid JSON") from e
    return parse(d)


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def forward(model: Plain, row) -> float:
    """p for one feature vector, exactly as the README defines it (missing flags included).
    The order is the ONNX graph's: Sub, Div, Gemm (sum over inputs, then + bias), Relu, Gemm, Sigmoid."""
    n = len(model.inputs)
    if isinstance(row, (str, bytes)) or len(row) != n:
        raise PlainModelError(f"a row needs exactly {n} values")
    h = []
    for x, m, s in zip(row, model.mean, model.std):
        h.append(max(-CLAMP, min(CLAMP, (_num(x, "row value") - m) / s)))
    for i, (w, b) in enumerate(model.layers):
        out = []
        for unit, bias in zip(w, b):
            acc = 0.0
            for wi, hi in zip(unit, h):
                acc += hi * wi
            out.append(acc + bias)
        h = [max(v, 0.0) for v in out] if i < len(model.layers) - 1 else out
    return min(1.0, max(0.0, _sigmoid(h[0])))

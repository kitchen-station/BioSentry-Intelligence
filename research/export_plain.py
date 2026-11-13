"""Write the student as plain JSON next to its ONNX file (slice M5). Uses numpy and onnx, never MindSpore.

    python -m research.export_plain [--model <dir>]

Reads metadata.json, checks each exported ONNX file against its sha256 and its graph against the
exact graph `student.to_onnx` writes, then writes model.json (and model_no_temp.json). Exit 2 with
one line on any refusal. The format is in research/plain_student.py.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, numpy_helper

from research.plain_student import FORMAT, canonical_bytes, parse

DEFAULT = "models/tier_c/student_vitals"
OUT = {"all": "model.json", "no_temp": "model_no_temp.json"}
# the graph student.to_onnx writes: (op, inputs, outputs); not imported, since that module needs MindSpore
NODES = [("Sub", ["x", "mean"], ["centred"]), ("Div", ["centred", "std"], ["h0"]),
         ("Gemm", ["h0", "w0", "b0"], ["g0"]), ("Relu", ["g0"], ["h1"]),
         ("Gemm", ["h1", "w1", "b1"], ["g1"]), ("Sigmoid", ["g1"], ["p"])]


class Refused(Exception):
    pass


def _graph(data: bytes, n: int) -> dict:
    m = onnx.load_from_string(data)
    if [(o.domain, o.version) for o in m.opset_import] != [("", 13)]:
        raise Refused("ONNX opset must be 13")
    g = m.graph
    if [(x.op_type, list(x.input), list(x.output)) for x in g.node] != NODES or any(x.attribute for x in g.node):
        raise Refused("ONNX graph is not the expected Sub, Div, Gemm, Relu, Gemm, Sigmoid with no attributes")
    inits = {i.name: i for i in g.initializer}
    if set(inits) != {"mean", "std", "w0", "b0", "w1", "b1"} or len(g.initializer) != 6:
        raise Refused("ONNX initialisers are not mean, std, w0, b0, w1, b1")
    if any(i.data_type != TensorProto.FLOAT for i in g.initializer):
        raise Refused("ONNX initialisers must be float32")
    a = {k: numpy_helper.to_array(v) for k, v in inits.items()}
    h = a["b0"].shape[0] if a["b0"].ndim == 1 else -1
    want = {"mean": (1, n), "std": (1, n), "w0": (n, h), "b0": (h,), "w1": (h, 1), "b1": (1,)}
    if h < 1 or any(a[k].shape != s for k, s in want.items()):
        raise Refused(f"ONNX initialiser shapes do not fit {n} inputs")
    if not all(np.isfinite(v).all() for v in a.values()):
        raise Refused("ONNX holds a non-finite number")
    return a


def _floats(a: np.ndarray):
    return a.astype(np.float32).tolist()  # a Python float of each float32 value: repr round-trips it


def export_variant(folder: Path, tag: str, top: dict, meta: dict) -> bytes:
    files = meta.get("files")
    name = files.get("onnx") if isinstance(files, dict) else None
    if not isinstance(name, str) or name != Path(name).name or not name.endswith(".onnx"):
        raise Refused(f"{tag}: metadata names no usable ONNX file")
    try:
        data = (folder / name).read_bytes()
    except OSError:
        raise Refused(f"{tag}: cannot read {name}") from None
    if hashlib.sha256(data).hexdigest() != meta.get("onnx_sha256"):
        raise Refused(f"{tag}: {name} does not match the sha256 in metadata.json")
    inputs = meta.get("features_student")
    if not isinstance(inputs, list) or not inputs:
        raise Refused(f"{tag}: metadata has no student feature list")
    try:
        a = _graph(data, len(inputs))
    except Refused as e:
        raise Refused(f"{tag}: {e}") from None
    t = ((meta.get("thresholds") or {}).get("student") or {}).get("threshold")
    if isinstance(t, bool) or not (t is None or (isinstance(t, (int, float)) and math.isfinite(t))):
        raise Refused(f"{tag}: threshold is not a number")
    d = {"format": FORMAT, "name": top.get("name"), "variant": tag, "inputs": inputs,
         "mean": _floats(a["mean"][0]), "std": _floats(a["std"][0]),
         "layers": [{"weights": _floats(a["w0"].T), "bias": _floats(a["b0"])},
                    {"weights": _floats(a["w1"].T), "bias": _floats(a["b1"])}],
         "hidden_activation": "relu", "output": "sigmoid", "threshold": None if t is None else float(t),
         "source": {"framework": top.get("framework"), "framework_version": top.get("framework_version"),
                    "onnx_file": name, "onnx_sha256": meta["onnx_sha256"]},
         "synthetic": top.get("synthetic"), "validated": top.get("validated")}
    try:
        parse(json.loads(canonical_bytes(d)))  # the reader must accept what is written
    except ValueError as e:
        raise Refused(f"{tag}: {e}") from None
    return canonical_bytes(d)


def run(folder: Path) -> dict[str, bytes | None]:
    """File name -> bytes to write, or None for a variant that is not exported (its file is stale)."""
    try:
        top = json.loads((folder / "metadata.json").read_bytes())
        variants = top["variants"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("cannot read metadata.json with a variants table") from None
    done = {}
    for tag, out in OUT.items():
        meta = variants.get(tag) if isinstance(variants, dict) else None
        done[out] = export_variant(folder, tag, top, meta) if isinstance(meta, dict) and meta.get("files") else None
    if not any(done.values()):
        raise Refused("no variant is exported")
    return done


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="research.export_plain")
    p.add_argument("--model", default=DEFAULT)
    folder = Path(p.parse_args(argv).model)
    try:
        done = run(folder)
        for out, data in done.items():
            if data is None:
                (folder / out).unlink(missing_ok=True)  # no stale file for a variant that is not exported
                continue
            tmp = folder / (out + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(folder / out)
            print(f"{out} {len(data)} bytes sha256 {hashlib.sha256(data).hexdigest()}")
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import hashlib
import json
import shutil
from pathlib import Path

import mindspore as ms
import numpy as np
import onnx
import pytest
from mindspore import Tensor, nn
from onnx import helper

from research import export_plain, student, train_student
from research.plain_student import canonical_bytes, forward, load
from test_student import TINY, built, tfs  # noqa: F401  (fixtures and the tiny grid, reused as they are)

COMMITTED = Path(__file__).parents[2] / "models" / "tier_c" / "student_vitals"


def batch(mean, std, seed=7):
    """1,000 fixed rows: 800 spread over mean +- 4 std, 200 of them with exact 0/1 in a random half of the columns."""
    rng = np.random.default_rng(seed)
    mean, std = np.asarray(mean, np.float32), np.asarray(std, np.float32)
    x = (mean + rng.uniform(-4, 4, (1000, len(mean))) * std).astype(np.float32)
    mask = rng.random((200, len(mean))) < 0.5
    x[800:] = np.where(mask, rng.integers(0, 2, (200, len(mean))).astype(np.float32), x[800:])
    return x


def plain_p(model, x):
    return np.array([forward(model, [float(v) for v in row]) for row in x])


def onnx_p(data, x):
    return student.onnx_predict(student.session(data), x).astype(np.float64)


@pytest.fixture(scope="module")
def tiny(built, tfs, tmp_path_factory):  # noqa: F811
    root = tmp_path_factory.mktemp("tiny")
    mp = pytest.MonkeyPatch()
    mp.setattr(train_student, "GRID", TINY)
    try:
        train_student.write_outputs(root, built, tfs[:2], tfs[2:], {}, True)
    finally:
        mp.undo()
    assert export_plain.main(["--model", str(root)]) == 0
    return root


def copy(tiny, tmp_path):
    return Path(shutil.copytree(tiny, tmp_path / "m"))


def exported(tiny):
    tags = [t for t, f in export_plain.OUT.items() if (tiny / f).exists()]
    assert tags, "the tiny build exported no variant"
    return tags


def test_plain_forward_matches_onnx_mindspore_and_mindir(tiny, built):  # noqa: F811
    for tag in exported(tiny):
        model = load(tiny / export_plain.OUT[tag])
        r = built.results[tag]
        x = batch(model.mean, model.std)
        p = plain_p(model, x)
        assert np.max(np.abs(p - onnx_p((tiny / f"model{'' if tag == 'all' else '_no_temp'}.onnx").read_bytes(), x))) <= 1e-6
        cell = student.Cell(r.student)
        cell.set_train(False)
        assert np.max(np.abs(p - student.forward(cell, x))) <= 1e-5
        graph = nn.GraphCell(ms.load(str(tiny / f"model{'' if tag == 'all' else '_no_temp'}.mindir")))
        p_ir = np.array([graph(Tensor(x[i:i + 1])).asnumpy()[0, 0] for i in range(len(x))], np.float64)
        assert np.max(np.abs(p - p_ir)) <= 1e-5
        assert list(model.inputs) == r.meta["features_student"] and model.variant == tag
        t = r.meta["thresholds"]["student"]["threshold"]
        assert model.threshold == (None if t is None else float(t))


def test_the_weights_round_trip_exactly(tiny, built):  # noqa: F811
    tag = exported(tiny)[0]
    st, model = built.results[tag].student, load(tiny / export_plain.OUT[tag])
    assert np.array_equal(np.array(model.mean, np.float32), st.mean)
    assert np.array_equal(np.array(model.layers[0][0], np.float32), st.layers[0][0].T)  # weights[unit][input]
    assert np.array_equal(np.array(model.layers[1][0], np.float32), st.layers[1][0].T)
    assert np.array_equal(np.array(model.layers[1][1], np.float32), st.layers[1][1])


def test_exporting_twice_gives_identical_bytes(tiny, tmp_path):
    folder = copy(tiny, tmp_path)
    before = {f: (folder / f).read_bytes() for f in export_plain.OUT.values() if (folder / f).exists()}
    assert export_plain.main(["--model", str(folder)]) == 0
    assert before == {f: (folder / f).read_bytes() for f in before}
    for f, data in before.items():
        assert data == canonical_bytes(json.loads(data))


def test_a_tampered_onnx_byte_is_refused(tiny, tmp_path, capsys):
    folder = copy(tiny, tmp_path)
    path = folder / "model.onnx" if (folder / "model.onnx").exists() else folder / "model_no_temp.onnx"
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    assert export_plain.main(["--model", str(folder)]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "sha256" in err


def test_a_wrong_sha256_in_the_metadata_is_refused(tiny, tmp_path, capsys):
    folder = copy(tiny, tmp_path)
    meta = json.loads((folder / "metadata.json").read_text())
    tag = exported(tiny)[0]
    meta["variants"][tag]["onnx_sha256"] = "0" * 64
    (folder / "metadata.json").write_bytes(canonical_bytes(meta))
    assert export_plain.main(["--model", str(folder)]) == 2
    assert capsys.readouterr().err.count("\n") == 1


def test_a_graph_with_an_unexpected_op_is_refused_even_with_a_matching_sha(tiny, tmp_path, capsys):
    folder = copy(tiny, tmp_path)
    tag = exported(tiny)[0]
    meta = json.loads((folder / "metadata.json").read_text())
    path = folder / meta["variants"][tag]["files"]["onnx"]
    m = onnx.load_from_string(path.read_bytes())
    relu = next(n for n in m.graph.node if n.op_type == "Relu")
    relu.CopyFrom(helper.make_node("Tanh", list(relu.input), list(relu.output)))
    path.write_bytes(m.SerializeToString())
    meta["variants"][tag]["onnx_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "metadata.json").write_bytes(canonical_bytes(meta))
    assert export_plain.main(["--model", str(folder)]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "graph" in err


def test_a_missing_metadata_is_refused(tmp_path, capsys):
    assert export_plain.main(["--model", str(tmp_path)]) == 2
    assert capsys.readouterr().err.count("\n") == 1


@pytest.mark.skipif(not (COMMITTED / "model.json").exists(),
                    reason="skipped until the exporter has been run on the real model (models/tier_c/student_vitals/model.json)")
def test_the_committed_json_is_a_fresh_export_and_matches_the_committed_models():
    fresh = export_plain.run(COMMITTED)
    for name, data in fresh.items():
        if data is None:
            assert not (COMMITTED / name).exists()
            continue
        assert (COMMITTED / name).read_bytes() == data
        model = load(COMMITTED / name)
        x = batch(model.mean, model.std)
        p = plain_p(model, x)
        stem = "model" if name == "model.json" else "model_no_temp"
        assert np.max(np.abs(p - onnx_p((COMMITTED / f"{stem}.onnx").read_bytes(), x))) <= 1e-6
        if (COMMITTED / f"{stem}.mindir").exists():
            graph = nn.GraphCell(ms.load(str(COMMITTED / f"{stem}.mindir")))
            p_ir = np.array([graph(Tensor(x[i:i + 1])).asnumpy()[0, 0] for i in range(len(x))], np.float64)
            assert np.max(np.abs(p - p_ir)) <= 1e-5

"""The golden fixture is what the research code gives today (needs numpy, MindSpore, onnxruntime)."""

import json
from pathlib import Path

import numpy as np

from research import student, wiring_fixture
from research.trials import Row

ROOT = Path(__file__).parent.parent.parent
GOLDEN = ROOT / "research" / "tests" / "data" / "wiring" / "golden.json"
G = json.loads(GOLDEN.read_text())
VARIANTS = {"all": True, "no_temp": False}
ONNX = {"all": "models/tier_c/student_vitals/model.onnx", "no_temp": "models/tier_c/student_vitals/model_no_temp.onnx"}


def stay_rows(s: dict) -> dict[int, Row]:
    rows = {}
    for b in s["bins"]:
        r = b["row"]
        if r is not None:
            rows[b["minute"]] = Row(s["id"], b["minute"], r["heart_rate"], r["respiratory_rate"], r["spo2"],
                                    r["temperature"], r["systolic"], r["bp_minute"], r["consciousness"],
                                    r["on_oxygen"], r["spo2_scale"], b["news2"]["ranked"], b["news2"]["total"])
    return rows


def test_regenerating_gives_the_committed_bytes(tmp_path):
    out = tmp_path / "golden.json"
    assert wiring_fixture.main(["--out", str(out)]) == 0
    assert out.read_bytes() == GOLDEN.read_bytes()


def test_features_and_baseline_flags_match_the_research_code():
    for s in G["stays"]:
        minutes, x = student.stay_features(stay_rows(s))
        scored = [b for b in s["bins"] if b["student"] is not None]
        assert minutes == [b["minute"] for b in scored]
        for v, temp in VARIANTS.items():
            got = student.view(x.astype(np.float32), temp, "trend")
            names = student.feature_names(temp, "trend")
            assert names == G["feature_names"][v]
            for row, b in zip(got, scored):
                assert [float(a) for a in row] == b["student"][v]["features"]
                for n in (n for n in names if n.endswith("_z_missing")):  # 0.0 when the baseline formed
                    assert bool(row[names.index(n)] == 0.0) is b["baseline_formed"][n[: -len("_z_missing")]]


def test_p_matches_onnx_within_1e_6():
    for v in VARIANTS:
        sess = student.session((ROOT / ONNX[v]).read_bytes())
        scored = [b["student"][v] for s in G["stays"] for b in s["bins"] if b["student"] is not None]
        p = student.onnx_predict(sess, np.array([e["features"] for e in scored], dtype=np.float32))
        assert max(abs(float(a) - e["p"]) for a, e in zip(p, scored)) <= 1e-6

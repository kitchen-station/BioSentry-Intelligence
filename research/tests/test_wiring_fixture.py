"""The committed golden fixture checks itself with the standard library only (no numpy)."""

import hashlib
import json
from pathlib import Path

from research.plain_student import forward, load
from research.rescore import score_row
from research.trials import Row

ROOT = Path(__file__).parent.parent.parent
GOLDEN = Path(__file__).parent / "data" / "wiring" / "golden.json"
TEXT = GOLDEN.read_text()
G = json.loads(TEXT)
MODEL = {"all": "models/tier_c/student_vitals/model.json", "no_temp": "models/tier_c/student_vitals/model_no_temp.json"}
VARIANTS = tuple(MODEL)
SCORED = [(s, b) for s in G["stays"] for b in s["bins"] if b["student"] is not None]


def test_p_is_the_plain_forward_pass_on_the_stored_features():
    for v in VARIANTS:
        m = load(ROOT / MODEL[v])
        for _, b in SCORED:
            f = b["student"][v]
            assert len(f["features"]) == len(G["feature_names"][v])
            assert forward(m, f["features"]) == f["p"]


def test_check_follows_p_t_q():
    for v in VARIANTS:
        t, q = G["constants"]["variants"][v]["t"], G["constants"]["variants"][v]["q"]
        for _, b in SCORED:
            p = b["student"][v]["p"]
            want = "quiet" if p < t else "alert" if q is not None and p > q else "not_sure"
            assert b["student"][v]["check"] == want


def test_policy_alerting_follows_the_documented_rule():
    """Score = weighted sum of z (form sum) or the largest weighted z (form max); alerting when the score is
    at least theta in p consecutive scored bins; a gap or a NOT RANKED bin resets the run."""
    for v in VARIANTS:
        c = G["constants"]["variants"][v]
        w = [c["policy_params"]["weights"][n] for n in G["constants"]["policy_vitals"]]
        theta, p = c["policy_params"]["theta"], c["policy_params"]["p"]
        for s in G["stays"]:
            run = 0
            for b in s["bins"]:
                if b["policy"] is None:
                    run = 0
                    continue
                e = b["policy"][v]
                parts = [wi * zi for wi, zi in zip(w, e["z"])]
                score = sum(parts) if c["policy_form"] == "sum" else max(parts)
                assert abs(score - e["score"]) < 1e-9
                run = run + 1 if score >= theta else 0
                assert e["alerting"] is (run >= p)


def test_news2_is_the_backend_scorer_on_the_stored_row():
    n = 0
    for s in G["stays"]:
        for b in s["bins"]:
            r = b["row"]
            if r is None:
                assert b["news2"] is None
                continue
            row = Row(s["id"], b["minute"], r["heart_rate"], r["respiratory_rate"], r["spo2"], r["temperature"],
                      r["systolic"], r["bp_minute"], r["consciousness"], r["on_oxygen"], r["spo2_scale"], False, None)
            got = score_row(row)
            total = getattr(got, "total", None)
            tp = sum(x.score for x in got.parameters if x.parameter == "temperature") if total is not None else None
            assert b["news2"] == {"ranked": total is not None, "total": total, "temperature_points": tp}
            n += 1
    assert n > 100


def test_bins_cover_every_15_minute_boundary():
    for s in G["stays"]:
        ms = [b["minute"] for b in s["bins"]]
        assert ms == list(range(ms[0], ms[-1] + 1, 15))
        for b in s["bins"]:
            assert (b["row"] is None) == (b["news2"] is None) == (b["baseline_formed"] is None)
            if b["row"] is not None and not b["news2"]["ranked"]:
                assert b["student"] is None and b["policy"] is None and b["policy_run_reset"] is True


def test_file_hashes_match_the_committed_files():
    assert len(G["files"]) == 8
    for f in G["files"]:
        data = (ROOT / f["path"]).read_bytes()
        assert (f["bytes"], f["sha256"]) == (len(data), hashlib.sha256(data).hexdigest())


def test_the_fixture_exercises_every_case():
    long = next(s for s in G["stays"] if s["source"] == "synthetic long stay")
    assert len(long["bins"]) == 94
    after = [b for b in long["bins"] if b["minute"] > 24 * 15]
    assert any(b["row"] is None for b in after)
    assert any(b["row"] is not None and not b["news2"]["ranked"] for b in after)
    assert all(all(b["baseline_formed"].values()) for b in after if b["row"] is not None)
    rows = [b["row"] for b in long["bins"] if b["row"] is not None]
    assert len({r["systolic"] for r in rows}) > 1 and len({r["bp_minute"] for r in rows}) > 1
    assert {r["on_oxygen"] for r in rows} == {True, False}
    for v in VARIANTS:
        assert {b["student"][v]["check"] for b in after if b["student"]} == {"alert", "not_sure", "quiet"}
        assert {b["policy"][v]["alerting"] for b in after if b["policy"]} == {True, False}
    gaps = [b["minute"] // 15 for b in long["bins"] if b["row"] is None]
    nr = [b["minute"] // 15 for b in long["bins"] if b["row"] is not None and not b["news2"]["ranked"]]
    assert len(gaps) == 2 and len(nr) == 2, (gaps, nr)
    for v in VARIANTS:  # the test's own counters, on the stored z values
        c = G["constants"]["variants"][v]
        w = [c["policy_params"]["weights"][n] for n in G["constants"]["policy_vitals"]]
        theta, p = c["policy_params"]["theta"], c["policy_params"]["p"]
        run = free = 0  # free: a counter that a gap or a NOT RANKED bin never resets
        after = {"gap": 0, "nr": 0}
        suppressed = 0
        for b in long["bins"]:
            if b["policy"] is None:
                after["gap" if b["row"] is None else "nr"] += run > 0
                run = 0
                continue
            e = b["policy"][v]
            hit = sum(wi * zi for wi, zi in zip(w, e["z"])) >= theta
            run, free = (run + 1, free + 1) if hit else (0, 0)
            suppressed += free >= p and not e["alerting"]
        assert after["gap"] >= 1 and after["nr"] >= 1 and suppressed >= 1, (v, after, suppressed)
        got = G["summary"]["totals"]["resets_after_run"][v]
        assert got == {"gap": after["gap"], "not_ranked": after["nr"], "suppressed_alerts": suppressed}
    totals = G["summary"]["totals"]
    for v in VARIANTS:
        assert sum(totals["check"][v].values()) == totals["scored_bins"] == len(SCORED)
        assert totals["policy_alerting"][v] > 0 and totals["policy_not_alerting"][v] > 0
    assert all(p["bins"] == len(s["bins"]) for p, s in zip(G["summary"]["per_stay"], G["stays"]))
    assert G["format"] == "biosentry-wiring-golden/1" and len(TEXT) < 1_000_000


def test_no_absolute_path():
    def strings(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, dict):
            for k, v in x.items():
                yield k
                yield from strings(v)
        elif isinstance(x, list):
            for v in x:
                yield from strings(v)

    for s in strings(G):
        assert not s.startswith(("/", "~")) and ":\\" not in s
    assert "/Users/" not in TEXT and "/home/" not in TEXT

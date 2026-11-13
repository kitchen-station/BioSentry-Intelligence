"""PARITY, the judge: the backend's features, p, check, z, score, alerting and NEWS2 against the research team's
reference file, bin by bin, on bins stored the way the simulator posts them. The file holds the research
values WITHOUT the product's gates, so the first test calls the ungated computation and the second the gates.
Never adjust a tolerance or special-case a bin: a mismatch is a finding."""

from pathlib import Path

from golden import at, load, points, stored_observations  # isort: skip

from biosentry.domain.computed import Computed, NotComputed
from biosentry.domain.scoring.tier_a import News2Score
from biosentry.domain.scoring.tier_b.reward_policy import alert_states, score
from biosentry.domain.scoring.tier_c.confidence import check_calibrated
from biosentry.domain.scoring.tier_c.features import policy_z, student_features
from biosentry.domain.scoring.tier_c.plain_mlp import forward
from biosentry.services.as_of import news2_as_of
from biosentry.services.model_signals import FORMING, NOT_RANKED, prepare_series, signals
from biosentry.services.models import load_models

MODELS = load_models(Path(__file__).resolve().parents[3] / "models")
VITALS = ("heart_rate", "respiratory_rate", "spo2", "temperature")


def stays() -> list[dict]:
    return load()["stays"]


def prepared(stay: dict, upto: int | None = None):  # type: ignore[no-untyped-def]
    prep = prepare_series(points(stay, upto), at(stay["admit_minute"]))
    assert not isinstance(prep, NotComputed)
    return prep


def test_the_reference_file_loads_models_and_is_the_pinned_one() -> None:
    assert not isinstance(MODELS.student, NotComputed) and not isinstance(MODELS.policy, NotComputed)
    assert not isinstance(MODELS.calibration, NotComputed)
    summary = load()["summary"]["totals"]
    assert summary["scored_bins"] == 128


def test_every_scored_bin_matches_the_reference_exactly() -> None:
    student, cal, policy = MODELS.student, MODELS.calibration, MODELS.policy
    counts = dict.fromkeys(("bins", "scored", "features", "p", "check", "z", "score", "alerting", "news2"), 0)
    seen_check: set[str] = set()
    for stay in stays():
        prep, rows = prepared(stay), stored_observations(stay)
        assert len(prep.series["heart_rate"]) == len(stay["bins"])
        scores: list[tuple[int, float | None]] = []
        expected: dict[int, bool] = {}
        for j, b in enumerate(stay["bins"]):
            if b["row"] is None:
                scores.append((15 * j, None))
                continue
            counts["bins"] += 1
            rb = news2_as_of(prep.device(j), rows, prep.at(j), earlier=False)
            assert rb is not None
            ranked = isinstance(rb.result, News2Score)
            assert ranked == b["news2"]["ranked"], (stay["id"], b["minute"])
            if ranked:
                assert rb.result.total == b["news2"]["total"], (stay["id"], b["minute"])  # type: ignore[union-attr]
            counts["news2"] += 1
            if not ranked:
                assert b["student"] is None and b["policy"] is None
                scores.append((15 * j, None))
                continue
            counts["scored"] += 1
            ref = b["student"]["all"]
            x = student_features(prep.series, j, news2_total=rb.result.total,  # type: ignore[union-attr]
                                 on_oxygen=bool(rb.inputs.supplemental_o2),
                                 consciousness_alert=str(rb.inputs.consciousness) == "A")
            for name, mine, theirs in zip(load()["feature_names"]["all"], x, ref["features"]):
                assert mine == theirs, (stay["id"], b["minute"], name, mine, theirs)
            counts["features"] += 1
            p = forward(student, x)
            assert abs(p - ref["p"]) <= 1e-12, (stay["id"], b["minute"], p, ref["p"])
            counts["p"] += 1
            state = check_calibrated(cal, p)
            assert state == ref["check"], (stay["id"], b["minute"], state, ref["check"])
            seen_check.add(state)
            counts["check"] += 1
            pol = b["policy"]["all"]
            z = policy_z(prep.series, j)
            assert all(abs(m - r) <= 1e-12 for m, r in zip(z, pol["z"])), (stay["id"], b["minute"], z, pol["z"])
            counts["z"] += 1
            s = score(policy.params, z)
            assert abs(s - pol["score"]) <= 1e-12, (stay["id"], b["minute"], s, pol["score"])
            counts["score"] += 1
            scores.append((15 * j, s))
            expected[j] = pol["alerting"]
        states = alert_states(policy.params, scores)
        for (minute, _), state in zip(scores, states):
            if minute // 15 in expected:
                assert state == expected[minute // 15], (stay["id"], minute, state)
                counts["alerting"] += 1
    assert counts["scored"] == 128 and counts["alerting"] == 128 and counts["news2"] == counts["bins"]
    assert seen_check == {"alert", "not_sure", "quiet"}
    print("PARITY COUNTS", counts)


def test_the_gates_on_the_same_stays_use_the_files_baseline_formed() -> None:
    computed = 0
    for stay in stays():
        rows = stored_observations(stay)
        for j, b in enumerate(stay["bins"]):
            if b["row"] is None:
                continue
            got = signals(prepared(stay, j), rows, MODELS, run_id=1, synthetic=True, computed_at=at(0))
            if not b["news2"]["ranked"]:
                assert (got.probability, got.check, got.policy) == (NotComputed(NOT_RANKED),) * 3
            elif not all(b["baseline_formed"].values()):
                assert (got.probability, got.check, got.policy) == (NotComputed(FORMING),) * 3
            else:
                ref, pol = b["student"]["all"], b["policy"]["all"]
                assert isinstance(got.probability, Computed) and isinstance(got.check, Computed)
                assert abs(got.probability.value - ref["p"]) <= 1e-12 and got.check.value == ref["check"]
                assert got.policy == Computed(pol["alerting"], got.policy.provenance, got.policy.window)  # type: ignore[union-attr]
                computed += 1
            if stay["id"] != "synthetic-long":
                assert not isinstance(got.probability, Computed)  # the toy stays never compute
    assert computed > 0

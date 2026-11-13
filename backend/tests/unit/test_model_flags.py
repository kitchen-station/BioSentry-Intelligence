"""A model flag adds a reason line and changes nothing else (T1). A flag needs the check to say alert (T2).

Both are attack targets: the tests try every input the models can give, and each says what must stay impossible.
"""

import hashlib
import inspect
import itertools
import random
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
import test_arbiter as ta
from samples import model_provenance  # isort: skip

from biosentry.domain import arbiter
from biosentry.domain.arbiter import (
    _LEVEL,
    PREDICTION_FLAG,
    POLICY_FLAG,
    NotRanked,
    PolicySignal,
    Ranked,
    TierBSignal,
    TierCSignal,
    arbitrate,
    rank_ward,
)
from biosentry.domain.computed import Computed, NotComputed, Window
from biosentry.domain.scoring.tier_a import News2Inputs, score_news2
from biosentry.domain.scoring.tier_c.confidence import Check
from biosentry.schemas import Acvpu, EscalationRule, Provenance, Spo2Scale
from biosentry.services import model_signals as ms
from biosentry.services import models as loader
from biosentry.services.model_signals import ModelProvenance, ModelSignals
from biosentry.services.ward import row_models

STUDENT = Provenance.model_validate(model_provenance("C", ms.STUDENT_ID))
CHECK = Provenance.model_validate(model_provenance("C", ms.CHECK_ID))
POLICY = Provenance.model_validate(model_provenance("B", ms.POLICY_ID))
WINDOW = Window(0.0, 900.0, 1.0)
BIN_END = datetime(2026, 1, 1, 12, tzinfo=UTC)
MODEL_TEXTS = (PREDICTION_FLAG, POLICY_FLAG)


def got(value: Any, prov: Provenance = STUDENT) -> Computed[Any]:
    return Computed(value, prov, WINDOW)


NONE = NotComputed("not computed")
PROBABILITIES = (got(0.0), got(0.5), got(1.0), NONE)
CHECKS = (got(Check.ALERT, CHECK), got(Check.NOT_SURE, CHECK), got(Check.QUIET, CHECK), NONE)
POLICIES = (got(True, POLICY), got(False, POLICY), NONE)
INPUTS = list(itertools.product(PROBABILITIES, CHECKS, POLICIES))  # every combination the models can give


def signals(p: Any, c: Any, a: Any) -> tuple[TierCSignal, PolicySignal]:
    return TierCSignal(p, c), PolicySignal(a)


def reachable() -> dict[tuple[int, int], Any]:
    """Every (level, total) the scorer can reach, with a score that has it."""
    kinds = {}
    for rr, hr, sbp, temp, o2 in itertools.product(
        (16, 10, 22, 26), (70, 45, 35, 120, 135), (120, 105, 95, 85), (37.0, 38.5, 39.5), (False, True)
    ):
        score = ta.news2(
            respiratory_rate=rr, heart_rate=hr, systolic_bp=sbp, temperature=temp, supplemental_o2=o2
        )  # fmt: skip
        kinds[(_LEVEL[score.risk], score.total)] = score
    return kinds


# --- T1: a model can move a row (must be impossible) -----------------------------------------


def test_t1a_no_model_input_changes_the_sort_key() -> None:
    """Every reachable (level, total) x baseline flag x id x every model input: the key is the plain key."""
    kinds = reachable()
    assert len(kinds) > 8
    plain: dict[tuple[Any, bool, str], Ranked] = {}
    for kind, flag, name in itertools.product(kinds, (False, True), ("p-a", "p-b", "p-c")):
        tier_b = TierBSignal((ta.drifting(),)) if flag else None
        base = arbitrate(name, kinds[kind], tier_b=tier_b)
        assert isinstance(base, Ranked)
        plain[(kind, flag, name)] = base
        for p, c, a in INPUTS:
            tier_c, policy = signals(p, c, a)
            decision = arbitrate(name, kinds[kind], tier_b=tier_b, tier_c=tier_c, policy=policy)
            assert isinstance(decision, Ranked)
            assert decision.sort_key == base.sort_key
            assert (decision.news2, decision.escalated, decision.flagged) == (base.news2, base.escalated, base.flagged)
    # Two patients, one with every flag and one with none: the order is the plain order, both ways.
    everything = signals(got(1.0), got(Check.ALERT, CHECK), got(True, POLICY))
    for (k1, base1), (k2, base2) in itertools.product(plain.items(), plain.items()):
        one = arbitrate(k1[2], kinds[k1[0]], tier_b=TierBSignal((ta.drifting(),)) if k1[1] else None,
                        tier_c=everything[0], policy=everything[1])  # fmt: skip
        assert isinstance(one, Ranked)
        assert (one.sort_key < base2.sort_key) == (base1.sort_key < base2.sort_key)


def wards(seed: int, count: int = 40) -> list[list[tuple[str, Any, Any]]]:
    """Seeded random wards: (id, News2Result, TierBSignal or None) for each patient."""
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        patients = []
        for i in range(rng.randint(2, 12)):
            inputs = News2Inputs(
                respiratory_rate=rng.choice((8, 10, 16, 22, 26)),
                spo2=rng.choice((89, 93, 95, 98)),
                spo2_scale=Spo2Scale.scale_1,
                supplemental_o2=rng.random() < 0.3,
                systolic_bp=rng.choice((None, 85, 95, 105, 120, 230)) if rng.random() < 0.1 else rng.choice((85, 120)),
                heart_rate=rng.choice((35, 45, 70, 95, 120, 135)),
                consciousness=rng.choice((Acvpu.A, Acvpu.A, Acvpu.V)),
                temperature=rng.choice((35.0, 37.0, 38.5, 39.5)),
            )
            tier_b = TierBSignal((ta.drifting(),)) if rng.random() < 0.4 else None
            patients.append((f"p-{rng.randint(0, 99):02d}-{i}", score_news2(inputs), tier_b))
        out.append(patients)
    return out


def decisions(ward: list[tuple[str, Any, Any]], mode: str, rng: random.Random) -> list[Any]:
    out = []
    for pid, result, tier_b in ward:
        p, c, a = rng.choice(INPUTS) if mode == "on" else (NONE, NONE, NONE)
        tier_c, policy = signals(p, c, a)
        out.append(arbitrate(pid, result, tier_b=tier_b, tier_c=tier_c, policy=policy))
    return out


def test_t1b_a_ward_ranks_the_same_with_models_on_off_and_refused() -> None:
    flagged = 0
    for ward in wards(20261114):
        off = rank_ward(arbitrate(pid, r, tier_b=b) for pid, r, b in ward)
        refused = rank_ward(decisions(ward, "refused", random.Random(1)))
        on = rank_ward(decisions(ward, "on", random.Random(7)))
        for other in (refused, on):
            assert [(rank, d.patient_id) for rank, d in other.ranked] == [(rank, d.patient_id) for rank, d in off.ranked]
            assert [d.patient_id for d in other.not_ranked] == [d.patient_id for d in off.not_ranked]
            for (_, a), (_, b) in zip(other.ranked, off.ranked, strict=True):
                assert (a.news2, a.escalated, a.flagged) == (b.news2, b.escalated, b.flagged)
                assert a.reasons[: len(b.reasons)] == b.reasons  # the old reasons first, unchanged
                assert {r.text for r in a.reasons[len(b.reasons) :]} <= set(MODEL_TEXTS)
        assert all(a.reasons == b.reasons for (_, a), (_, b) in zip(refused.ranked, off.ranked, strict=True))
        flagged += sum(len(d.reasons) for _, d in on.ranked) - sum(len(d.reasons) for _, d in off.ranked)
    assert flagged > 50  # the generated wards do carry flags: the comparison above is not empty


def test_t1d_the_sort_key_text_is_pinned() -> None:
    """A change to `Ranked.sort_key` must be deliberate: change this hash in the same commit, with a reason."""
    text = inspect.getsource(Ranked.sort_key.fget)  # type: ignore[union-attr]
    assert hashlib.sha256(text.encode()).hexdigest() == "4d0472b6356692cdd9c72f07886f089b7bca8aeefd5e182ade9f891788c491e5"


RANKING_TESTS = (
    "test_ward_order_is_trigger_level_then_total_then_id",
    "test_a_single_three_outranks_a_higher_total_without_a_trigger",
    "test_ordering_is_independent_of_input_order",
    "test_equal_scores_are_broken_by_patient_id",
    "test_rank_is_relative_to_the_set_given",
    "test_rank_ward_accepts_a_generator",
    "test_the_flag_only_breaks_ties_between_equal_totals_below_a_trigger",
)


def test_t1f_every_ranking_test_of_the_arbiter_holds_with_both_flags_on_every_patient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flags = signals(got(1.0), got(Check.ALERT, CHECK), got(True, POLICY))
    real = arbiter.arbitrate
    seen: list[Any] = []

    def flagged(patient_id: str, tier_a: Any, **hooks: Any) -> Any:
        decision = real(patient_id, tier_a, **hooks, **({"tier_c": flags[0], "policy": flags[1]}))
        seen.append((real(patient_id, tier_a, **hooks), decision))
        return decision

    monkeypatch.setattr(ta, "arbitrate", flagged)
    for name in RANKING_TESTS:
        getattr(ta, name)()
    ranked_pairs = [(a, b) for a, b in seen if isinstance(a, Ranked)]
    assert len(ranked_pairs) > 100
    for plain, with_flags in ranked_pairs:
        assert isinstance(with_flags, Ranked)
        assert replace(with_flags, reasons=plain.reasons) == plain  # every other field is identical
        assert [r.text for r in with_flags.reasons[len(plain.reasons) :]] == list(MODEL_TEXTS)
    assert all(replace(a, inert=()) == replace(b, inert=()) for a, b in seen if isinstance(a, NotRanked))


# --- T2: a flag without the check saying alert (must be impossible) ---------------------------


def test_t2a_the_prediction_reason_is_there_if_and_only_if_the_check_says_alert() -> None:
    for p, c in itertools.product(PROBABILITIES, CHECKS):
        decision = arbitrate("p-1", ta.news2(), tier_c=TierCSignal(p, c))
        assert isinstance(decision, Ranked)
        alert = isinstance(c, Computed) and c.value is Check.ALERT
        assert (PREDICTION_FLAG in [r.text for r in decision.reasons]) is alert
        assert not [r for r in decision.reasons if r.rule is EscalationRule.alert_policy_flag]
    for a in POLICIES:
        decision = arbitrate("p-1", ta.news2(), policy=PolicySignal(a))
        assert isinstance(decision, Ranked)
        assert (POLICY_FLAG in [r.text for r in decision.reasons]) is (isinstance(a, Computed) and a.value is True)


def test_the_two_reasons_come_after_the_others_and_carry_their_own_rules() -> None:
    decision = arbitrate("p-1", ta.news2(heart_rate=35), tier_b=TierBSignal((ta.drifting(),)),
                         tier_c=TierCSignal(got(1.0), got(Check.ALERT, CHECK)), policy=PolicySignal(got(True, POLICY)))  # fmt: skip
    assert isinstance(decision, Ranked)
    assert [r.rule for r in decision.reasons][-2:] == [EscalationRule.prediction_model_flag, EscalationRule.alert_policy_flag]
    assert decision.reasons[-3].rule is EscalationRule.tier_b_finding
    assert decision.flagged and decision.escalated  # from the finding and the trigger, not from a model
    assert decision.inert == ()


def test_a_flag_alone_sets_neither_escalated_nor_flagged() -> None:
    decision = arbitrate("p-1", ta.news2(), tier_c=TierCSignal(got(1.0), got(Check.ALERT, CHECK)),
                         policy=PolicySignal(got(True, POLICY)))  # fmt: skip
    assert isinstance(decision, Ranked)
    assert (decision.escalated, decision.flagged) == (False, False)
    assert [r.text for r in decision.reasons] == list(MODEL_TEXTS)


def response(p: Any, c: Any, a: Any) -> ModelSignals:
    """What C1 gives for a result: a Computed value has its provenance entry (the bin)."""
    found = {}
    for model_id, results in ((ms.STUDENT_ID, (p, c)), (ms.POLICY_ID, (a,))):
        if any(isinstance(r, Computed) for r in results):
            found[model_id] = ModelProvenance(model_id, "all", "x", "ab" * 32, "m1", 1, BIN_END.replace(hour=11, minute=45), BIN_END)
    return ModelSignals(p, c, a, found)


def test_t2d_the_state_is_flag_if_and_only_if_the_reason_is_in_the_row() -> None:
    reached = [(p, c, a) for p, c, a in INPUTS if isinstance(c, NotComputed) or isinstance(p, Computed)]  # C1 never gives a check without p
    assert reached
    seen = set()
    for p, c, a in reached:
        decision = arbitrate("p-1", ta.news2(), tier_c=TierCSignal(p, c), policy=PolicySignal(a))
        assert isinstance(decision, Ranked)
        texts = [r.text for r in decision.escalation_reasons()]
        models = row_models(response(p, c, a))
        assert (models.prediction.state == "flag") is (PREDICTION_FLAG in texts)
        assert (models.alert_policy.state == "flag") is (POLICY_FLAG in texts)
        seen |= {models.prediction.state, models.alert_policy.state}
    assert seen == {"flag", "no_flag", "not_sure", "not_computed"}


def test_the_state_and_the_fields_of_each_result() -> None:
    quiet = row_models(response(got(0.001), got(Check.QUIET, CHECK), got(False, POLICY)))
    assert (quiet.prediction.state, quiet.alert_policy.state) == ("no_flag", "no_flag")
    unsure = row_models(response(got(0.01), got(Check.NOT_SURE, CHECK), got(True, POLICY)))
    assert (unsure.prediction.state, unsure.alert_policy.state) == ("not_sure", "flag")
    one = unsure.prediction
    assert (one.reason, one.bin_end, [p.model_id for p in one.provenance]) == (None, BIN_END, [ms.STUDENT_ID, ms.CHECK_ID])
    assert one.bin_start == BIN_END.replace(hour=11, minute=45)
    assert [p.model_id for p in unsure.alert_policy.provenance] == [ms.POLICY_ID]
    assert unsure.alert_policy.provenance[0].tier.value == "B"
    # The student computes but the calibration does not: the prediction is not computed, with the check's reason.
    no_cal = row_models(response(got(1.0), NotComputed(loader.NO_CALIBRATION), got(False, POLICY)))
    assert no_cal.prediction.state == "not_computed" and no_cal.prediction.reason == loader.NO_CALIBRATION
    assert (no_cal.prediction.bin_start, no_cal.prediction.bin_end, no_cal.prediction.provenance) == (None, None, [])
    assert no_cal.alert_policy.state == "no_flag"
    assert "probability" not in no_cal.model_dump_json() and "1.0" not in no_cal.prediction.model_dump_json()


def test_the_not_sure_state_adds_no_reason_and_is_not_no_flag() -> None:
    decision = arbitrate("p-1", ta.news2(), tier_c=TierCSignal(got(0.01), got(Check.NOT_SURE, CHECK)))
    assert isinstance(decision, Ranked) and decision.reasons == ()
    state = row_models(response(got(0.01), got(Check.NOT_SURE, CHECK), NONE)).prediction.state
    assert state == "not_sure" != "no_flag"


# --- Words ---------------------------------------------------------------------------------


def our_strings() -> list[str]:
    fixed = [v for k, v in vars(ms).items() if k.isupper() and isinstance(v, str) and " " in v]
    loaded = [loader.NO_STUDENT, loader.NO_CALIBRATION, loader.NO_POLICY]
    return [*MODEL_TEXTS, *fixed, *loaded]


def test_t2e_no_text_renders_not_sure_as_safe_or_normal() -> None:
    assert len(our_strings()) > 10
    for text in our_strings():
        assert not re.search(r"\b(safe|normal|low risk|no flag)\b", text, re.I), text
    for state in ("flag", "no_flag", "not_sure", "not_computed"):
        assert not re.search(r"safe|normal|low risk", state)


def test_no_new_text_makes_a_claim_it_cannot_back() -> None:
    for text in our_strings():
        assert not re.search(r"guarantee|AI predicts|reinforcement|self-learning|fewer false alarms", text, re.I), text
        assert not re.search(r"(?<!not )validated", text, re.I), text
    for text in MODEL_TEXTS:
        assert "synthetic, not validated" in text


def test_a_model_value_names_its_file_and_run() -> None:
    plain = model_provenance("C", "x")
    for left in ("variant", "file_sha256", "run_id"):
        with pytest.raises(ValueError, match="needs variant"):
            Provenance.model_validate({**plain, left: None})
    with pytest.raises(ValueError):
        Provenance.model_validate({**plain, "file_sha256": "AB" * 32})  # lower-case hex only
    with pytest.raises(ValueError):
        Provenance.model_validate({**plain, "file_sha256": "ab" * 31})
    device = {**plain, "source": "device", "tier": None, "variant": None, "file_sha256": None, "run_id": None}
    assert Provenance.model_validate(device).variant is None  # other sources are unchanged
    assert Provenance.model_validate({k: v for k, v in device.items() if k not in ("variant", "file_sha256", "run_id")})


def test_the_three_file_fields_are_served_only_for_a_model_value() -> None:
    plain = model_provenance("C", "x")
    assert {"variant", "file_sha256", "run_id"} <= set(Provenance.model_validate(plain).model_dump())
    device = {k: v for k, v in {**plain, "source": "device", "tier": None}.items() if k not in ("variant", "file_sha256", "run_id")}
    assert not {"variant", "file_sha256", "run_id"} & set(Provenance.model_validate(device).model_dump())

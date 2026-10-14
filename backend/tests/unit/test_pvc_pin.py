"""The pin that freezes the PVC method before the test split is scored (ectopy/pin.json).

Everything the Evaluator's conditions name is checked against the files it freezes: the code by
sha256, the weights by sha256 and by threshold, the records, the folds, the comparators' parameters
against the development output, the minimums and the claim words. Edit any frozen file and these
fail: that is the point, an edit after the pin is an amendment, not a tidy-up.
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml

from biosentry.domain.registry import Registry

ROOT = Path(__file__).resolve().parents[3]
TRAINING = ROOT / "training"
MODEL_DIR = ROOT / "models" / "tier_b" / "ectopy"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TRAINING / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rb = _load("rr_baseline")
pvc = _load("pvc")
head = _load("pvc_head")


def _pin() -> dict[str, Any]:
    data = json.loads((MODEL_DIR / "pin.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- what is frozen is what is on disk -----------------------------------------------------------


def test_the_pinned_code_and_weights_are_the_files_in_the_tree() -> None:
    pin = _pin()
    assert set(pin["code_sha256"]) == {
        "training/rr_baseline.py",
        "training/pvc.py",
        "training/pvc_head.py",
        "training/rr_baseline_selection.json",
    }
    for rel, digest in pin["code_sha256"].items():
        assert _sha(ROOT / rel) == digest, f"{rel} changed after the pin: that is an amendment"
    assert _sha(MODEL_DIR / "weights.npz") == pin["head"]["weights_sha256"]
    raw = yaml.safe_load((ROOT / "models" / "registry.yaml").read_text(encoding="utf-8"))
    entry = next(m for m in Registry.model_validate(raw).models if m.id == pin["head"]["id"])
    assert entry.sha256 == pin["head"]["weights_sha256"]


def test_the_pinned_threshold_and_constants_are_the_weights_and_the_code() -> None:
    pin = _pin()
    weights = np.load(MODEL_DIR / "weights.npz")
    assert np.float32(pin["head"]["threshold"]) == weights["threshold"][0]
    assert pin["head"]["threshold"] in head.THRESHOLDS
    assert pin["head"]["gbm"] == head.GBM
    assert pin["head"]["features"] == list(head.FEATURE_NAMES)
    assert pin["detector"]["threshold_fraction"] == pvc.DETECTOR_FRACTION
    selection = json.loads((TRAINING / "rr_baseline_selection.json").read_text(encoding="utf-8"))
    carried = {k: v for k, v in pin["detector"].items() if k in selection["detector"]}
    expected = dict(selection["detector"])
    expected["threshold_fraction"] = pvc.DETECTOR_FRACTION
    assert carried == expected


def test_the_comparators_are_the_documented_rules_with_parameters_from_the_grids() -> None:
    comp = _pin()["comparators"]
    assert comp["primary"]["theta"] in pvc.THETAS
    assert comp["secondary"]["theta"] in pvc.THETAS
    assert comp["secondary"]["phi"] in pvc.PHIS
    assert "never picked after scoring" in comp["secondary"]["reported"]
    assert comp["primary"]["name"] == "RR-ratio rule"
    assert comp["primary"]["median_needs"] == "at least 4 detections in the window"
    assert pvc.MIN_CONTEXT_BEATS == 4


# --- the records ----------------------------------------------------------------------------------


def test_the_record_lists_are_disjoint_complete_and_exclude_what_was_ruled_out() -> None:
    rec = _pin()["records"]
    dev = rec["development"]
    test = rec["test_split"]["records"]
    excluded = set(rec["excluded"])
    assert dev == list(rb.DS1)
    assert len(test) == 21
    assert len(set(test)) == 21
    assert not set(dev) & set(test)
    assert "201" in dev
    assert "202" not in test
    assert excluded == {"202", "102", "104", "107", "217"}
    assert not excluded & set(dev)
    assert not excluded & set(test)
    assert len(dev) + len(test) + len(excluded) == 48  # every record of the database, once
    assert rec["test_split"]["name"] == (
        "inter-patient test split of MIT-BIH Arrhythmia (DS2), same database as the "
        "development split"
    )


def test_the_folds_are_the_ones_the_code_deals_out_and_are_record_grouped() -> None:
    rec = _pin()["records"]
    folds = rec["development_folds"]
    assert head.make_folds(rec["development_v_counts"]) == folds
    flat = [n for f in folds for n in f]
    assert sorted(flat) == sorted(rec["development"])  # each record in exactly one fold
    assert len(set(flat)) == len(flat)
    assert len(folds) == 5
    assert "201" in flat


def test_the_test_records_exist_only_in_the_pin_not_in_any_code_file() -> None:
    test = _pin()["records"]["test_split"]["records"]
    for path in sorted(TRAINING.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not [r for r in test if f'"{r}"' in text], path.name


# --- definitions, minimums, statistics, claim words -----------------------------------------


def test_the_definitions_say_what_the_evaluator_accepted() -> None:
    d = _pin()["definitions"]
    assert d["positive"] == "annotated V only"
    assert "F (fusion) and E (escape) included" in d["negatives"]
    assert "stay in the denominators" in d["negatives"]
    assert "within 150 ms" in d["match"]
    assert pvc.MATCH_TOL_S == 0.150
    assert "matched to an annotated beat or not" in d["ppv"]
    assert "a beat the detector missed is not called PVC" in d["specificity"]
    assert "records with no PVCs are listed and stay in the denominators" in d["pooling"]
    assert "shifted by half a window" in d["window_placement"]
    assert pvc.POSITIVE == "V"
    assert "60 samples either side" in d["callable"]
    assert head.HALF == 60


def test_the_minimums_and_the_statistics_are_the_rulings() -> None:
    pin = _pin()
    assert pin["minimums"]["sensitivity"] == 0.80
    assert pin["minimums"]["ppv"] == 0.80
    assert "point estimates" in pin["minimums"]["basis"]
    assert "not gating" in pin["minimums"]["intervals"]
    assert "does not depend on beating the comparator" in pin["minimums"]["comparator"]
    boot = pin["statistics"]["bootstrap"]
    assert boot == {"unit": "record", "resamples": rb.N_BOOT, "seed": rb.SEED}
    assert (rb.N_BOOT, rb.SEED) == (1000, 20261014)
    assert "head minus primary comparator" in pin["statistics"]["paired"]


def test_the_pin_is_pending_and_says_that_the_test_split_is_reserved_not_scored() -> None:
    pin = _pin()
    assert pin["status"] == "pending"
    assert pin["purpose"].startswith("DS2 reserved, not scored.")
    note = pin["status_note"]
    assert "DS2 reserved, not scored" in note
    assert "DS2 is unconsumed" in note
    assert "valid only for a head that is frozen and scored once" in note
    assert "a different head needs a new pin" in note
    assert "Applies only if the test split is scored" in pin["consumption"]
    assert pin["claims"]["allowed"].startswith("None for PVC detection while DS2 is not scored.")


def test_the_disclosure_the_consumption_rule_and_the_claim_words_are_in_the_pin() -> None:
    pin = _pin()
    assert "3,220 V in 16 of 22 records, 202 included" in pin["disclosure"]
    assert "No DS2 signal, detector output or method result had been computed" in pin["disclosure"]
    assert "scored once" in pin["consumption"]
    assert "a mismatch is reported, not fixed" in pin["consumption"]
    assert "no decision about the method may be informed by it" in pin["purpose"]
    claims = pin["claims"]
    assert (
        "inter-patient test split of MIT-BIH Arrhythmia (DS2), same database as the"
        in claims["allowed"]
    )
    assert {"external", "validated"} <= set(claims["never"])
    assert "PVC across devices and sites untested" in claims["limitations_to_state"]


def test_the_development_numbers_in_the_pin_are_the_ones_in_the_registry_and_the_card() -> None:
    pin = _pin()
    oof = pin["head"]["development_out_of_fold"]
    raw = yaml.safe_load((ROOT / "models" / "registry.yaml").read_text(encoding="utf-8"))
    entry = next(m for m in Registry.model_validate(raw).models if m.id == "tier_b/ectopy")
    assert entry.metrics["ds1_oof_sensitivity"] == pytest.approx(oof["sensitivity"], abs=5e-4)
    assert entry.metrics["ds1_oof_ppv"] == pytest.approx(oof["ppv"], abs=5e-4)
    assert entry.metrics["ds1_oof_specificity"] == pytest.approx(oof["specificity"], abs=5e-4)
    card = " ".join((MODEL_DIR / "card.md").read_text(encoding="utf-8").split())
    primary = pin["comparators"]["primary"]["theta"]
    assert f"< {primary:.2f}" in card
    row = f"| Learned head, out-of-fold, threshold {pin['head']['threshold']:.2f} |"
    assert f"{row} {oof['sensitivity']:.3f} " in card

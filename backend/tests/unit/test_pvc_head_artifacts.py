"""The PVC head's committed artifacts: pinned by sha256, holding trees and nothing else (P6).

`weights.npz` is the artifact the registry pins. It holds the boosted trees as arrays, the
band-pass coefficients, a few constants and eight SYNTHETIC parity rows; never a recording. Array
names, shapes and dtypes are pinned exactly, and the parity rows are regenerated from their seed.
"""

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
import yaml

from biosentry.domain.registry import Registry, RegistryEntry, RegistryStatus

ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = ROOT / "models" / "tier_b" / "ectopy"
WEIGHTS = MODEL_DIR / "weights.npz"
CARD = MODEL_DIR / "card.md"
TRAINING = ROOT / "training"
N_TREES = 60
N_GOLDEN = 8


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


def _entry() -> RegistryEntry:
    raw = yaml.safe_load((ROOT / "models" / "registry.yaml").read_text(encoding="utf-8"))
    return next(m for m in Registry.model_validate(raw).models if m.id == "tier_b/ectopy")


def _params() -> dict[str, np.ndarray]:
    got: dict[str, np.ndarray] = head.load_params(WEIGHTS)
    return got


def expected_arrays(width: int) -> dict[str, tuple[tuple[int, ...], str]]:
    return {
        "tree_feature": ((N_TREES, width), "int16"),
        "tree_threshold": ((N_TREES, width), "float64"),
        "tree_left": ((N_TREES, width), "int16"),
        "tree_right": ((N_TREES, width), "int16"),
        "tree_value": ((N_TREES, width), "float64"),
        "baseline": ((1,), "float64"),
        "max_depth": ((1,), "int32"),
        "golden_snippets": ((N_GOLDEN, 120), "float32"),
        "golden_context": ((N_GOLDEN, 4), "float32"),
        "golden_features": ((N_GOLDEN, 12), "float64"),
        "golden_proba": ((N_GOLDEN,), "float64"),
        "sos": ((2, 6), "float64"),
        "threshold": ((1,), "float32"),
        "half": ((1,), "int32"),
        "clip": ((1,), "float32"),
        "mad_scale": ((1,), "float32"),
        "eps": ((1,), "float32"),
        "format": ((1,), "<U19"),
    }


# --- the registry pin ---------------------------------------------------------------------


def test_the_registry_pins_the_weights_file_by_sha256() -> None:
    digest = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()
    assert _entry().sha256 == digest, "weights.npz changed: re-export and update the registry"


def test_the_head_is_a_research_entry_with_a_verified_licence_and_one_corpus() -> None:
    entry = _entry()
    assert entry.status is RegistryStatus.research
    assert entry.licence == "https://physionet.org/content/mitdb/view-license/1.0.0/"
    assert len(entry.corpus) == 1
    assert entry.valid_until is None
    assert set(entry.metrics) == {"ds1_oof_sensitivity", "ds1_oof_ppv", "ds1_oof_specificity"}
    assert (ROOT / entry.card).is_file()


# --- trees and nothing else (P6) --------------------------------------------------------------


def test_weights_hold_exactly_the_named_arrays_and_no_object_arrays() -> None:
    with np.load(WEIGHTS, allow_pickle=False) as data:
        width = data["tree_feature"].shape[1]
        expected = expected_arrays(width)
        assert set(data.files) == set(expected)
        for name, (shape, dtype) in expected.items():
            array = data[name]
            assert array.shape == shape, name
            assert array.dtype == np.dtype(dtype), name
        assert width <= 15  # a depth-3 tree has at most 15 nodes
        # nothing here can hold a recording (a window alone is 2,500 samples): the biggest array
        # is a tree table (60 x 15) or the eight parity snippets (8 x 120)
        assert max(data[name].size for name in data.files) <= 1000


def test_every_tree_is_well_formed_and_the_node_count_is_what_the_card_says() -> None:
    p = _params()
    feat, left, right = p["tree_feature"], p["tree_left"], p["tree_right"]
    depth = int(p["max_depth"][0])
    assert depth == head.GBM["max_depth"]
    for t in range(N_TREES):
        stack = [(0, 0)]
        while stack:
            node, d = stack.pop()
            assert d <= depth, (t, node)
            if feat[t, node] >= 0:
                assert 0 <= feat[t, node] < head.N_FEATURES
                for child in (int(left[t, node]), int(right[t, node])):
                    assert 0 < child < feat.shape[1]
                    stack.append((child, d + 1))
    assert head.n_nodes(p) == 862


def test_the_threshold_and_constants_are_the_frozen_ones() -> None:
    p = _params()
    assert float(p["threshold"][0]) == pytest.approx(0.40)
    assert float(p["threshold"][0]) in {np.float32(t) for t in head.THRESHOLDS}
    assert (int(p["half"][0]), float(p["clip"][0])) == (head.HALF, head.CLIP)
    assert float(p["mad_scale"][0]) == pytest.approx(head.MAD_SCALE)
    assert float(p["eps"][0]) == pytest.approx(head.EPS)
    assert str(p["format"][0]) == "biosentry-pvc-gbm-1"


def test_the_band_pass_is_the_one_the_rhythm_head_uses() -> None:
    arrhythmia = np.load(ROOT / "models" / "tier_b" / "arrhythmia" / "weights.npz")
    assert np.allclose(_params()["sos"], arrhythmia["sos"], rtol=0, atol=1e-12)


# --- the parity rows are synthetic and seeded (P6) ------------------------------------------


def test_the_parity_rows_are_the_seeded_generators_output_not_recordings() -> None:
    p = _params()
    snips, ctx, window = head.golden_inputs()
    assert np.allclose(p["golden_snippets"], snips, atol=1e-6)
    assert np.array_equal(p["golden_context"], ctx)
    features = head.beat_features(snips, np.ones(N_GOLDEN, dtype=bool), window, ctx)
    assert np.allclose(p["golden_features"], features, atol=1e-6)


def test_the_numpy_trees_reproduce_the_fitted_models_probabilities_on_the_parity_rows() -> None:
    p = _params()
    got = head.predict_proba(p, p["golden_features"])
    assert np.abs(got - p["golden_proba"]).max() < 1e-9
    assert ((got > 0.0) & (got < 1.0)).all()
    assert got.max() - got.min() > 0.05  # the rows are not all the same case


# --- the card carries what the rulings asked it to carry --------------------------------------


def test_the_card_carries_the_licence_lines_the_exclusions_and_the_claim_words() -> None:
    card = " ".join(CARD.read_text(encoding="utf-8").split())  # the card is hard-wrapped
    for needle in (
        "Open Data Commons Attribution License v1.0",
        "Anyone can access the files, as long as they conform to the terms of the specified",
        "10.13026/C2F305",
        "https://physionet.org/content/mitdb/view-license/1.0.0/",
        "the paced records 102, 104, 107, 217",
        "202 (same subject and tape as 201",
        "3,220 `V` in 16 of 22 records, 202 included",
        "No DS2 signal, detector output or method result has been computed on DS2",
        "inter-patient test split of MIT-BIH Arrhythmia (DS2), same database as the",
        'not a test result and not "external" or "validated"',
        "pooled sensitivity >= 0.80 and PPV >= 0.80 as point estimates",
        "PVC across devices and sites is untested",
        "The M3 exit gate is not met by this card",
        "minimum NOT met on development data; DS2 reserved, not scored; no PVC claim",
        "DS2 is reserved and not scored",
        "This card makes no PVC claim; the head is research-only",
        "wired nowhere",
        "a different head needs a new pin",
        "out-of-fold development estimates, not a test result",
        "The RR-ratio rule (0.610 / 0.573 / 0.966) is the PVC baseline",
        "It does not rank",
        "never trained, tuned or calibrated on BioSentry patients",
    ):
        assert needle in card, needle
    assert "held-out" not in card.lower()

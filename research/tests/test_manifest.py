"""Slice M4: the manifest of model files and the registry table. Tmp folders only, no real model."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from research import manifest as mf


def put(root: Path, rel: str, data) -> bytes:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = data if isinstance(data, bytes) else (json.dumps(data, indent=1, sort_keys=True) + "\n").encode()
    p.write_bytes(raw)
    return raw


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


REGISTRY = f"""# Registry

Hand-written intro.

{mf.BEGIN}

old table

{mf.END}

## Datasets

| ward-v2 | hand written |

<!-- language-model: filled by the research-agent session -->
their text
<!-- /language-model -->
"""


@pytest.fixture
def repo(tmp_path):
    r = tmp_path
    put(r, "models/tier_c/student_vitals/model.onnx", b"onnx-all")
    put(r, "models/tier_c/student_vitals/model_no_temp.onnx", b"onnx-nt")
    put(r, "models/tier_c/student_vitals/model.mindir", b"mindir-all")
    put(r, "models/tier_c/student_vitals/model.json", {"format": "biosentry-plain-mlp-1"})
    put(r, "models/tier_c/student_vitals/metadata.json", {
        "framework": "MindSpore", "framework_version": "2.11.0", "seeds_read": {"train": [61, 62, 63, 70], "validation": [261, 262]},
        "variants": {"all": {"parameters": {"student": 25}}, "no_temp": {"parameters": {"student": 40}}}})
    put(r, "models/tier_c/student_vitals/card.md", b"card")
    put(r, "models/tier_c/student_vitals/lite.txt", b"text")
    put(r, "training/runs/student_vitals/m1-old.json", {
        "model": "tier_c/student_vitals", "run_id": "m1-old", "commit": "0000000", "framework_version": "2.10.0",
        "data": {"train_seeds": [61], "validation_seeds": [261]}, "variants": {}})
    put(r, "training/runs/student_vitals/m1-new.json", {
        "model": "tier_c/student_vitals", "run_id": "m1-new", "commit": "abc1234", "framework_version": "2.11.0",
        "data": {"train_seeds": [61, 62, 63, 70], "validation_seeds": [261, 262]},
        "variants": {"all": {"parameters": {"student": 25}}, "no_temp": {"parameters": {"student": 40}}}})
    pol = put(r, "models/tier_b/reward_policy/policy.json", {"seeds": {"first": 401, "last": 500}, "validation_seeds": [341, 342, 343]})
    put(r, "models/tier_b/reward_policy/no_temperature/policy.json", {"seeds": {"first": 401, "last": 480}, "validation_seeds": [341, 342, 343]})
    put(r, "models/tier_b/reward_policy/rounds/round-2/policy.json", {"dropped": True})
    put(r, "models/tier_b/reward_policy/rounds/round-1/gen-00.json", {})
    put(r, "models/tier_b/reward_policy/experiments.jsonl", b"{}\n")
    put(r, "training/runs/reward_policy/m2.json", {"model": "tier_b/reward_policy", "run_id": "m2-1", "commit": "def5678",
                                                    "runs": [{"ledger": {"shipped": {"policy_sha256": sha(pol)}}}]})
    cal = put(r, "models/tier_c/confidence_check/calibration.json", {"seeds": [301, 302, 303]})
    put(r, "models/tier_c/confidence_check/calibration.json.sha256", (sha(cal) + "\n").encode())
    put(r, "models/tier_c/confidence_check/heldout.txt", b"text")
    put(r, "training/runs/confidence_check/m3.json", {"model": "tier_c/confidence_check", "run_id": "m3-1", "commit": None,
                                                       "variants": {"all": {"calibration_sha256": sha(cal)}}})
    put(r, "models/tier_b/baseline/params.json", {"k": 4.0, "p": 8, "chosen_on": "ward-v2 seeds 1-10"})
    put(r, "models/tier_a/news2/card.md", b"card")
    put(r, "models/data/ward-v2/card.md", b"card")
    put(r, "models/README.md", b"readme")
    put(r, "training/REGISTRY.md", REGISTRY.encode())
    return r


def run(root, *flags):
    return mf.main([*flags, "--root", str(root)])


def test_write_then_check_passes_and_the_bytes_are_deterministic(repo, capsys):
    assert run(repo, "--write") == 0
    first = (repo / "models/manifest.json").read_bytes()
    assert run(repo, "--write") == 0 and (repo / "models/manifest.json").read_bytes() == first
    assert run(repo, "--check") == 0 and capsys.readouterr().err == ""
    top = json.loads(first)
    paths = [m["path"] for m in top["models"]]
    assert paths == sorted(paths) and first.endswith(b"\n")
    assert paths == ["models/tier_b/baseline/params.json", "models/tier_b/reward_policy/no_temperature/policy.json",
                     "models/tier_b/reward_policy/policy.json", "models/tier_c/confidence_check/calibration.json",
                     "models/tier_c/student_vitals/model.json", "models/tier_c/student_vitals/model.mindir",
                     "models/tier_c/student_vitals/model.onnx", "models/tier_c/student_vitals/model_no_temp.onnx"]
    assert str(repo) not in first.decode() and not re.search(r'"/(app|tmp|Users|home|private|data)', first.decode())


def test_every_field_comes_from_the_files_and_their_metadata(repo):
    run(repo, "--write")
    got = {m["path"].split("/", 1)[1]: m for m in json.loads((repo / "models/manifest.json").read_bytes())["models"]}
    onnx = got["tier_c/student_vitals/model.onnx"]
    assert (onnx["bytes"], onnx["sha256"], onnx["format"], onnx["variant"]) == (8, sha(b"onnx-all"), "ONNX", "all")
    assert (onnx["framework"], onnx["framework_version"]) == ("MindSpore", "2.11.0")
    assert onnx["training_seeds"] == {"train": "61-63,70", "validation": "261-262"}
    assert (onnx["run_id"], onnx["commit"]) == ("m1-new", "abc1234") and "commit_note" not in onnx  # the matching log, not the old one
    assert got["tier_c/student_vitals/model.json"]["format"] == "biosentry-plain-mlp-1"
    assert got["tier_c/student_vitals/model.mindir"]["format"] == "MindIR"
    pol = got["tier_b/reward_policy/policy.json"]
    assert (pol["run_id"], pol["commit"], pol["framework_version"]) == ("m2-1", "def5678", None)
    assert pol["framework"].startswith("none") and pol["training_seeds"] == {"generations": "401-500", "validation": "341-343"}
    nt = got["tier_b/reward_policy/no_temperature/policy.json"]  # its sha256 is in no ledger line: nothing is guessed
    assert (nt["run_id"], nt["commit"]) == (None, None) and "no run log" in nt["commit_note"]
    cal = got["tier_c/confidence_check/calibration.json"]
    assert cal["run_id"] == "m3-1" and cal["commit"] is None and cal["commit_note"] == "the run log records no commit"
    assert cal["training_seeds"] == {"calibration": "301-303"}
    base = got["tier_b/baseline/params.json"]
    assert base["training_seeds"] == {"chosen_on": "ward-v2 seeds 1-10"} and base["commit"] is None and "sweep" in base["commit_note"]


def test_without_a_matching_run_log_the_ids_are_null_with_a_note(repo):
    (repo / "training/runs/student_vitals/m1-new.json").unlink()
    run(repo, "--write")
    m = next(x for x in json.loads((repo / "models/manifest.json").read_bytes())["models"] if x["path"].endswith("student_vitals/model.onnx"))
    assert (m["run_id"], m["commit"]) == (None, None) and "no run log" in m["commit_note"]


def test_a_changed_byte_a_missing_file_and_an_unlisted_model_file_fail_the_check(repo, capsys):
    run(repo, "--write")
    (repo / "models/tier_c/student_vitals/model.onnx").write_bytes(b"onnx-ALL")
    assert run(repo, "--check") == 2
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1  # same length, so only the hash differs
    assert any(l.startswith("models/tier_c/student_vitals/model.onnx: sha256 differs (manifest '") for l in err)
    run(repo, "--write")
    (repo / "models/tier_b/baseline/params.json").unlink()
    assert run(repo, "--check") == 2
    assert capsys.readouterr().err.splitlines()[-1] == "models/tier_b/baseline/params.json: listed in the manifest but the file is missing"
    run(repo, "--write")
    (repo / "models/tier_c/student_vitals/model.ms").write_bytes(b"lite")  # a listed kind of file that the manifest lacks
    assert run(repo, "--check") == 2
    assert capsys.readouterr().err.strip() == "models/tier_c/student_vitals/model.ms: model file is not listed in the manifest"
    (repo / "models/tier_c/student_vitals/model.ms").unlink()
    (repo / "models/tier_c/student_vitals/extra.onnx").write_bytes(b"x")  # a model file nobody listed in the rule
    assert run(repo, "--check") == 2 and "extra.onnx: neither a listed model file nor a record" in capsys.readouterr().err
    assert run(repo, "--write") == 2  # and --write refuses it too, instead of listing it silently


def test_a_changed_metadata_or_run_log_shows_up_as_a_named_field(repo, capsys):
    run(repo, "--write")
    put(repo, "training/runs/student_vitals/m1-new.json", {
        "model": "tier_c/student_vitals", "run_id": "m1-new", "commit": "9999999", "framework_version": "2.11.0",
        "data": {"train_seeds": [61, 62, 63, 70], "validation_seeds": [261, 262]},
        "variants": {"all": {"parameters": {"student": 25}}, "no_temp": {"parameters": {"student": 40}}}})
    assert run(repo, "--check") == 2
    assert "model.onnx: commit differs (manifest 'abc1234', now '9999999')" in capsys.readouterr().err


def test_a_missing_or_foreign_manifest_fails_clearly(repo, capsys):
    assert run(repo, "--check") == 2 and "models/manifest.json: missing" in capsys.readouterr().err
    (repo / "models/manifest.json").write_text("{not json")
    assert run(repo, "--check") == 2 and "missing or not a manifest" in capsys.readouterr().err


def test_records_are_not_models_by_a_fixed_rule():
    rec = ["tier_c/student_vitals/card.md", "tier_b/reward_policy/held_out.txt", "x/lite.txt", "x/metadata.json", "x/exports.json",
           "x/timing.json", "x/convert_lite.sh", "x/calibration.json.sha256", "tier_b/reward_policy/experiments.jsonl",
           "tier_b/reward_policy/rounds/round-2/policy.json", "tier_b/reward_policy/no_temperature/generations/gen-00.json"]
    assert all(mf.is_record(Path(p)) for p in rec)
    assert not any(mf.is_record(Path(p)) for p in ("a/model.onnx", "a/model.json", "a/policy.json", "a/new.bin", "a/params.json"))


def test_the_registry_regenerates_only_its_own_table(repo):
    run(repo, "--write")
    assert run(repo, "--registry") == 0
    text = (repo / "training/REGISTRY.md").read_text()
    head, rest = REGISTRY.split(mf.BEGIN)
    _, tail = rest.split(mf.END)
    assert text.startswith(head + mf.BEGIN) and text.endswith(mf.END + tail)  # hand-written parts byte for byte
    assert "their text" in text and "| ward-v2 | hand written |" in text and "old table" not in text
    rows = [l for l in text.splitlines() if l.startswith("| tier_")]
    assert len(rows) == 8 and "`models/tier_c/student_vitals/model.onnx`" in text and sha(b"onnx-all") in text
    assert "MindSpore 2.11.0" in text and "| 61-63,70" not in text and "train 61-63,70; validation 261-262" in text
    again = (repo / "training/REGISTRY.md").read_bytes()
    assert run(repo, "--registry") == 0 and (repo / "training/REGISTRY.md").read_bytes() == again
    assert mf.registry_is_current(repo)
    (repo / "training/REGISTRY.md").write_text(text.replace("model.onnx", "model.xnnx", 1))
    assert not mf.registry_is_current(repo)


def test_the_registry_needs_a_manifest_and_its_markers(repo, capsys):
    assert run(repo, "--registry") == 2 and "manifest" in capsys.readouterr().err
    run(repo, "--write")
    (repo / "training/REGISTRY.md").write_text("no markers here\n")
    assert run(repo, "--registry") == 2 and "markers" in capsys.readouterr().err
    assert (repo / "training/REGISTRY.md").read_text() == "no markers here\n"


def test_usage(repo, capsys):
    assert run(repo) == 2 and run(repo, "--write", "--check") == 2
    assert mf.main(["--bogus"]) == 2


def test_ranges():
    assert mf.ranges([61, 62, 63, 70, 72, 73]) == "61-63,70,72-73" and mf.ranges([5]) == "5"
    assert mf.ranges([]) is None and mf.ranges(None) is None and mf.ranges(["a"]) is None


MANIFEST = Path(__file__).parent.parent.parent / "models" / "manifest.json"


@pytest.mark.skipif(not MANIFEST.exists(), reason="TODO part 2: models/manifest.json is written once the real student run has finished")
def test_the_committed_manifest_and_registry_match_the_committed_files(capsys):
    assert mf.main([]) == 2  # no flag: usage
    capsys.readouterr()
    assert mf.main(["--check"]) == 0, capsys.readouterr().err
    assert mf.registry_is_current(mf.ROOT)

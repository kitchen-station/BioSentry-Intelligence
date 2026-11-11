import pytest

from helpers import TOY, copy_toy
from research.trials import TrialError, load_trial


def test_toy_loads():
    t = load_trial(TOY)
    assert (t.name, t.seed, t.days, t.speed, len(t.rows), len(t.episodes)) == ("toy", 11, 1, 60, 41, 4)
    assert t.patient_days == 2.5


REFUSALS = {
    "unknown format version": ("manifest.json", '"format_version": 1,\n  "synthetic"', '"format_version": 2,\n  "synthetic"', "unknown format_version"),
    "format version true": ("manifest.json", '"format_version": 1,\n  "synthetic"', '"format_version": true,\n  "synthetic"', "unknown format_version"),
    "not synthetic": ("manifest.json", '"synthetic": true', '"synthetic": false', "synthetic is not true"),
    "synthetic absent": ("manifest.json", '  "synthetic": true,\n', "", "synthetic is not true"),
    "bad bin": ("manifest.json", '"bin_minutes": 15', '"bin_minutes": 10', "bin_minutes"),
    "bad hash": ("manifest.json", '"scenario_sha256": "0', '"scenario_sha256": "g', "scenario_sha256"),
    "manifest not json": ("manifest.json", "{", "[", "not valid JSON"),
    "cohort header": ("cohort.csv", "patient,minute", "patient,minutes", "wrong header"),
    "episodes header": ("episodes.csv", "patient,admit_minute", "patient,admit", "wrong header"),
    "minute not a multiple of 15": ("cohort.csv", "p-001,15,", "p-001,20,", "multiple of 15"),
    "minute outside the trial": ("cohort.csv", "p-001,915,", "p-001,1440,", "inside the trial"),
    "patient not in episodes": ("cohort.csv", "p-001,15,", "x-999,15,", "x-999 is not in episodes.csv"),
    "duplicate row": ("cohort.csv", "p-001,30,", "p-001,15,", "twice"),
    "ranked without news2": ("cohort.csv", "p-001,15,70,16,97,36.8,120,70,5,A,0,1,100,1,0", "p-001,15,70,16,97,36.8,120,70,5,A,0,1,100,1,", "news2 must be set"),
    "short row": ("cohort.csv", "p-001,15,70,", "p-001,15,", "expected 15 fields"),
    "unknown consciousness": ("cohort.csv", "p-001,15,70,16,97,36.8,120,70,5,A", "p-001,15,70,16,97,36.8,120,70,5,X", "consciousness"),
    "patients count": ("manifest.json", '"patients": 4', '"patients": 5', "patients does not match"),
    "episodes count": ("manifest.json", '"episodes": 2', '"episodes": 1', "episodes does not match"),
    "episode fields half set": ("episodes.csv", "respiratory,300,90", "respiratory,,90", "all set or all empty"),
    "unknown episode kind": ("episodes.csv", "respiratory", "cardiac", "episode_kind"),
    "episode patient twice": ("episodes.csv", "n-0001,60", "p-001,60", "twice"),
    "outcome from NEWS2 is checked, not trusted": ("episodes.csv", "discharged", "sent_home", "outcome"),
    "negative patient days": ("summary.json", '"patient_days": 2', '"patient_days": -2', "patient_days"),
}


@pytest.mark.parametrize("case", REFUSALS)
def test_refusals(tmp_path, case):
    file, old, new, expect = REFUSALS[case]
    folder = copy_toy(tmp_path / "t", **{file: (old, new)})
    with pytest.raises(TrialError, match=expect) as e:
        load_trial(folder)
    assert "\n" not in str(e.value)


@pytest.mark.parametrize("name", ["manifest.json", "cohort.csv", "episodes.csv", "summary.json"])
def test_missing_file(tmp_path, name):
    folder = copy_toy(tmp_path / "t")
    (folder / name).unlink()
    with pytest.raises(TrialError, match=f"{name}: missing"):
        load_trial(folder)


def test_not_a_folder(tmp_path):
    with pytest.raises(TrialError, match="not a folder"):
        load_trial(tmp_path / "nothing")

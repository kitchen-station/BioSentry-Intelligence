import pytest

from helpers import TOY, copy_toy
from research.rescore import check_parity
from research.trials import TrialError, load_trial


def test_toy_parity_passes():
    assert check_parity(load_trial(TOY)) == 41


@pytest.mark.parametrize("old,new", [
    ("p-001,30,115,22,95,36.8,120,70,5,A,0,1,100,1,5", "p-001,30,115,22,95,36.8,120,70,5,A,0,1,100,1,6"),  # wrong total
    ("p-001,30,115,22,95,36.8,120,70,5,A,0,1,100,1,5", "p-001,30,70,22,95,36.8,120,70,5,A,0,1,100,1,5"),  # input changed
    ("p-001,30,115,22,95,36.8,120,70,5,A,0,1,100,1,5", "p-001,30,115,22,,36.8,120,70,5,A,0,1,100,1,5"),  # ranked but input missing
    ("n-0001,375,70,16,,36.8,120,70,5,A,0,1,40,0,", "n-0001,375,70,16,97,36.8,120,70,5,A,0,1,40,0,"),  # not ranked but scorable
])
def test_parity_fails_loudly_and_names_the_row(tmp_path, old, new):
    folder = copy_toy(tmp_path / "toy", **{"cohort.csv": (old, new)})
    patient, minute = old.split(",")[:2]
    with pytest.raises(TrialError, match=rf"toy: parity mismatch at patient {patient} minute {minute}"):
        check_parity(load_trial(folder))

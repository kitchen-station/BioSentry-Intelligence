import os
import re
from pathlib import Path

import pytest

import research.trials as trials_mod
from helpers import copy_toy
from research.trials import FILES, TrialError, load_trial


def test_only_the_four_fixed_names_are_opened(tmp_path, monkeypatch):
    (tmp_path / "secret.txt").write_text("x")
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,", "../secret.txt,15,")})
    opened = []
    real = os.open
    monkeypatch.setattr(os, "open", lambda p, *a, **k: (opened.append(Path(p)), real(p, *a, **k))[1])
    with pytest.raises(TrialError, match="bad patient id"):
        load_trial(folder)
    assert {p.name for p in opened} <= set(FILES) and all(p.parent == folder for p in opened)


def test_a_path_in_a_field_is_text_and_reads_nothing(tmp_path, monkeypatch):
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,70,", "p-001,15,../../etc/passwd,")})
    with pytest.raises(TrialError, match="heart_rate is not a number"):
        load_trial(folder)


def test_oversized_json_is_refused(tmp_path):
    folder = copy_toy(tmp_path / "t")
    (folder / "manifest.json").write_text(" " * (trials_mod.MAX_JSON_BYTES + 1))
    with pytest.raises(TrialError, match="larger than"):
        load_trial(folder)


def test_oversized_csv_and_too_many_rows_are_refused(tmp_path, monkeypatch):
    folder = copy_toy(tmp_path / "t")
    monkeypatch.setattr(trials_mod, "MAX_CSV_BYTES", 100)
    with pytest.raises(TrialError, match="larger than the 100 byte cap"):
        load_trial(folder)
    monkeypatch.undo()
    monkeypatch.setattr(trials_mod, "MAX_COHORT_ROWS", 5)
    with pytest.raises(TrialError, match="more than 5 rows"):
        load_trial(folder)


def test_over_long_line_is_refused(tmp_path):
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,", "p-001" + "a" * 2000 + ",15,")})
    with pytest.raises(TrialError, match="longer than 1000 characters"):
        load_trial(folder)


@pytest.mark.parametrize("field", ["=1+1", "@SUM(1,1)", "+cmd|' /C calc'!A0", "-2+3"])
def test_formula_fields_are_text_never_evaluated(tmp_path, field):
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,70,", f"p-001,15,{field.replace(',', ';')},")})
    with pytest.raises(TrialError, match="not a number") as e:
        load_trial(folder)
    assert repr(field.replace(",", ";")[:40]) in str(e.value)


def test_echoed_text_is_capped_and_control_characters_are_replaced(tmp_path):
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,70,", "p-001,15,\x1b[31m" + "z" * 100 + ",")})
    with pytest.raises(TrialError) as e:
        load_trial(folder)
    msg = str(e.value)
    assert "\x1b" not in msg and "\n" not in msg and "?[31m" in msg and "z" * 35 in msg and "z" * 36 not in msg


def test_symlinked_trial_file_is_refused_not_followed(tmp_path):
    folder = copy_toy(tmp_path / "t")
    outside = tmp_path / "outside.csv"
    outside.write_text((folder / "cohort.csv").read_text())
    (folder / "cohort.csv").unlink()
    (folder / "cohort.csv").symlink_to(outside)
    with pytest.raises(TrialError, match="cohort.csv: cannot be opened"):
        load_trial(folder)


def test_a_symlinked_folder_is_allowed(tmp_path):
    # The runner works through a symlinked path; only the four files inside are never links.
    folder = copy_toy(tmp_path / "t")
    link = tmp_path / "link"
    link.symlink_to(folder)
    assert load_trial(link).seed == 11


def test_a_fifo_in_place_of_a_file_is_refused_without_hanging(tmp_path):
    folder = copy_toy(tmp_path / "t")
    (folder / "summary.json").unlink()
    os.mkfifo(folder / "summary.json")
    with pytest.raises(TrialError, match="not a regular file"):
        load_trial(folder)


def test_package_never_touches_network_clock_or_random_source():
    root = Path(trials_mod.__file__).parent
    banned = re.compile(r"^\s*(import|from)\s+(socket|urllib|http|requests|time|datetime|random|secrets|subprocess)\b", re.M)
    uses_env = re.compile(r"os\.(environ|getenv|urandom)|eval\(|exec\(|pickle|yaml|__import__")
    for path in [*root.glob("*.py"), *(root / "tests").glob("*.py")]:
        text = path.read_text()
        assert not banned.search(text), path
        if path.name != "test_security.py":
            assert not uses_env.search(text), path


def test_a_directory_named_like_a_trial_file_is_refused_with_one_line(tmp_path, capsys):
    from research.evaluate import main
    folder = copy_toy(tmp_path / "t")
    (folder / "episodes.csv").unlink()
    (folder / "episodes.csv").mkdir()
    with pytest.raises(TrialError, match="episodes.csv: not a regular file"):
        load_trial(folder)
    assert main([str(folder)]) == 2
    assert capsys.readouterr().err.count("\n") == 1


def test_huge_integer_and_deep_nesting_in_json_are_refused(tmp_path):
    folder = copy_toy(tmp_path / "t")
    (folder / "manifest.json").write_text('{"format_version": ' + "9" * 5000 + "}")
    with pytest.raises(TrialError, match="manifest.json: not valid JSON"):
        load_trial(folder)
    (folder / "manifest.json").write_text("[" * 20000 + "]" * 20000)
    with pytest.raises(TrialError, match="manifest.json: not valid JSON"):
        load_trial(folder)


@pytest.mark.parametrize("digits", ["١٢", "１２", "²"])  # Arabic-Indic, fullwidth, superscript two
def test_digits_from_other_scripts_are_not_numbers(tmp_path, digits):
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,70,", f"p-001,15,{digits},")})
    with pytest.raises(TrialError, match="heart_rate is not a number"):
        load_trial(folder)
    folder2 = copy_toy(tmp_path / "t2", **{"cohort.csv": ("p-001,15,", f"p-001,{digits},")})
    with pytest.raises(TrialError, match="minute is not a number"):
        load_trial(folder2)
    folder3 = copy_toy(tmp_path / "t3", **{"episodes.csv": ("n-0001,60,", f"n-0001,{digits},")})
    with pytest.raises(TrialError, match="admit_minute is not a number"):
        load_trial(folder3)


def test_an_extra_column_is_refused(tmp_path):
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,70,16,97,36.8,120,70,5,A,0,1,100,1,0",
                                                        "p-001,15,70,16,97,36.8,120,70,5,A,0,1,100,1,0,7")})
    with pytest.raises(TrialError, match="expected 15 fields"):
        load_trial(folder)
    folder2 = copy_toy(tmp_path / "t2", **{"episodes.csv": ("n-0003,30,1100,discharged,,,,,,0", "n-0003,30,1100,discharged,,,,,,0,9")})
    with pytest.raises(TrialError, match="expected 10 fields"):
        load_trial(folder2)


def test_nul_and_bad_utf8_are_refused(tmp_path):
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,70,", "p-001,15,70\x00,")})
    with pytest.raises(TrialError, match="heart_rate is not a number"):
        load_trial(folder)
    folder2 = copy_toy(tmp_path / "t2", **{"cohort.csv": ("p-001,", "p\x00-001,")})
    with pytest.raises(TrialError, match="bad patient id"):
        load_trial(folder2)
    folder3 = copy_toy(tmp_path / "t3")
    (folder3 / "cohort.csv").write_bytes((folder3 / "cohort.csv").read_bytes().replace(b"p-001,15", b"p-\xff01,15", 1))
    with pytest.raises(TrialError, match="not valid UTF-8"):
        load_trial(folder3)


@pytest.mark.parametrize("field", ["nan", "NaN", "inf", "-inf", "Infinity", "0x10", "1_0", "+5", "1e3", ".5", "5.", "1,5", " 5", "5 ", "--5"])
def test_number_spellings_that_python_would_accept_are_refused(tmp_path, field):
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,70,", f"p-001,15,{field.replace(',', ';')},")})
    with pytest.raises(TrialError):
        load_trial(folder)


def test_bidirectional_and_zero_width_characters_are_neutralised_in_echoes(tmp_path):
    hostile = "‮⁦​﻿abc"
    folder = copy_toy(tmp_path / "t", **{"cohort.csv": ("p-001,15,70,", f"p-001,15,{hostile},")})
    with pytest.raises(TrialError) as e:
        load_trial(folder)
    msg = str(e.value)
    assert all(c not in msg for c in hostile[:4]) and "????abc" in msg

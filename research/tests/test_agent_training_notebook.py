"""The committed notebook that shows the fine-tuning run log. Standard library only."""

import html
import json
import re
import sys
import types
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[2] / "training" / "agent"
NOTEBOOK = AGENT / "notebooks" / "finetune.ipynb"

BANNED = ("mindspore", "mindnlp", "torch", "transformers", "peft", "train_lora", "run_model", "subprocess", "os.system")


def notebook():
    return json.loads(NOTEBOOK.read_text())


def test_the_notebook_is_executed_and_imports_nothing_heavy():
    nb = notebook()
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert nb["nbformat"] == 4 and len(code) >= 8
    for c in code:
        assert c["execution_count"] is not None and c["outputs"], c["source"][:60]
    for c in nb["cells"]:
        src = "".join(c["source"]).lower()
        assert not [w for w in BANNED if w in src], (c["cell_type"], src[:60])


def test_no_output_holds_a_path_or_a_user_name():
    out = json.dumps([c.get("outputs") for c in notebook()["cells"]])
    for bad in ("/Users/", "/home/", "/private/", "/tmp/", str(Path.home())):
        assert bad not in out, bad


def run_cells(monkeypatch, ipython):
    """Runs the code cells in order in one namespace from the notebook's folder; returns the namespace and the displays."""
    shown = []
    if ipython:
        mod = types.ModuleType("IPython.display")
        mod.HTML = lambda s: ("html", s)
        mod.display = shown.append
        monkeypatch.setitem(sys.modules, "IPython", types.ModuleType("IPython"))
        monkeypatch.setitem(sys.modules, "IPython.display", mod)
    else:
        monkeypatch.setitem(sys.modules, "IPython", None)
        monkeypatch.setitem(sys.modules, "IPython.display", None)
    ns = {"__name__": "notebook"}
    for i, c in enumerate(c for c in notebook()["cells"] if c["cell_type"] == "code"):
        types.FunctionType(compile("".join(c["source"]), f"cell{i}", "exec"), ns)()
    return ns, shown


def test_the_notebook_says_so_when_the_run_log_is_missing(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "training" / "agent" / "notebooks"
    folder.mkdir(parents=True)
    (tmp_path / "training" / "agent" / "runs").mkdir()
    (folder / "finetune.ipynb").write_text(NOTEBOOK.read_text())
    for ipython in (False, True):
        ns, shown = run_cells_from(folder, monkeypatch, ipython)
        assert ns["DOC"] is None and shown == []
    assert "does not exist yet" in capsys.readouterr().out


def run_cells_from(folder, monkeypatch, ipython):
    monkeypatch.chdir(folder)
    return run_cells(monkeypatch, ipython)


def table_lines(text_by_title, title):
    return next(v for k, v in text_by_title.items() if k.startswith(title))


def check_counts(tables, doc):
    """Every before/after row in `tables` (title -> lines) holds the exact counts of the run log's evaluations."""
    names = {"untuned": "untuned", "tuned": "tuned", "matcher": "keyword matcher", "perfect": "perfect answers"}
    for task, title in (("question", "Questions: before and after"), ("planner", "Planner: before and after")):
        table = table_lines(tables, title)
        for key, r in doc["evaluations"][task].items():
            row = next(line for line in table if line.startswith(names[key])).split(" | ")
            if not r["complete"]:
                assert f"incomplete: {r['answers']} of {r['of']} answers" in row
                continue
            m = r["scores"]
            want = [f"{m['valid_json']}/{m['n']}", f"{m['exact']}/{m['exact_of']}", f"{m['outside_schema']}/{m['n']}"]
            if task == "question":
                want += [f"{m['refused_ok']}/{m['negatives']}", f"{m['answerable_acted_right']}/{m['positives']}",
                         f"{m['refusal_acted']}/{m['negatives']}"]
            assert all(w in row for w in want), (task, key, want, row)


def stored_tables(nb):
    """The tables in the notebook's stored outputs, as title -> lines of 'a | b | c'."""
    out = {}
    for c in nb["cells"]:
        for o in c.get("outputs", []):
            h = "".join(o.get("data", {}).get("text/html", ""))
            if "<table>" not in h:
                continue
            h = re.sub(r"</t[dh]>", " | ", h).replace("</tr>", "\n").replace("</p>", "\n")
            text = html.unescape(re.sub(r"<[^>]+>", "", h))
            rows = [line.removesuffix(" | ").removesuffix(" |") for line in text.splitlines() if line]
            out[rows[0]] = rows[1:]
    return out


@pytest.mark.parametrize("ipython", [False, True])
def test_the_notebook_runs_from_the_committed_run_log(monkeypatch, ipython):
    assert (AGENT / "runs" / "run1.json").is_file(), "training/agent/runs holds no run log"
    ns, shown = run_cells_from(NOTEBOOK.parent, monkeypatch, ipython)
    doc = ns["DOC"]
    assert doc is not None, "the notebook's RUN names no file in training/agent/runs"
    assert bool(shown) == ipython
    if ipython:
        assert any("<svg" in s[1] for s in shown) or not doc["steps"]
    check_counts({t.splitlines()[0]: t.splitlines() for t in ns["TEXT"]}, doc)


def test_the_stored_outputs_come_from_the_committed_run_log():
    nb = notebook()
    run = next(c for c in nb["cells"] if c["cell_type"] == "code" and "RUN = " in "".join(c["source"]))
    run_id = re.search(r'RUN = "([^"]+)"', "".join(run["source"]))[1]
    doc = json.loads((AGENT / "runs" / f"{run_id}.json").read_text())
    first = "".join("".join(o.get("text", "")) for o in run["outputs"])
    assert f"training/agent/runs/{run_id}.json" in first and f"Run {doc['run_id']}." in first, "executed against another run log"
    stored = stored_tables(nb)
    check_counts(stored, doc)
    facts = " ".join(table_lines(stored, "Run facts"))
    tuned = doc["adapter"]["adapter_model.safetensors"]["sha256"]
    assert tuned is None or tuned in facts
    assert f"steps done {doc['training']['steps_done']}" in facts


def test_no_run_log_holds_a_path_or_a_user_name():
    for f in (AGENT / "runs").glob("*"):
        text = f.read_text()
        assert "/Users/" not in text and "/home/" not in text and str(Path.home()) not in text, f.name

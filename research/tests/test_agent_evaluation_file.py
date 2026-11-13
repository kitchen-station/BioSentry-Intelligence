"""The evaluation site's file for the research agent is made by the command, never by hand."""

import json
from pathlib import Path

from research.agent import __main__ as cli
from test_agent_ask import cfg  # noqa: F401  (only for a home outside the repository)
from test_agent_planner import env_for

REPO = Path(__file__).resolve().parents[2]
FILE = REPO / "frontend" / "src" / "data" / "evaluation" / "research-agent.json"
LEDGER = REPO / "research" / "agent" / "records" / "pr19-run" / "ledger.jsonl"
SCORES = REPO / "training" / "agent" / "runs" / "run1.scores.json"


def test_the_committed_file_is_exactly_what_the_export_writes(cfg, tmp_path):  # noqa: F811
    doc = json.loads(FILE.read_text())
    p = doc["provenance"]
    argv = ["export", "--date", p["date"], "--commit", p["commit"], "--ledger", str(LEDGER), "--out", str(tmp_path / "out.json")]
    for line in p["source"]:
        argv += ["--source", line]
    if doc["body"]["scores"] is not None:
        argv += ["--scores", str(SCORES)]
    assert cli.main(argv, env_for(cfg)) == 0
    assert (tmp_path / "out.json").read_bytes() == FILE.read_bytes()
    assert (doc["format"], doc["section"], p["synthetic"], p["validated"]) == ("biosentry-evaluation/1", "research-agent", True, False)

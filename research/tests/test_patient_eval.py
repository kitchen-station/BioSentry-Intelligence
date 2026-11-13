"""The matcher's score on the blind questions is the one the card states."""

import json
from collections import Counter
from pathlib import Path

from research.patient_agent import ask

HERE = Path(ask.__file__).parent
NAMES = {"baseline": "own_baseline", "changes": "recent_change", "rank": "why_rank", "alerts": "active_alerts"}  # the writer's names


def test_the_card_states_the_matcher_score_on_the_blind_questions():
    items = json.loads((HERE / "eval_questions.json").read_text())["items"]
    assert len(items) == 160 and len({i["id"] for i in items}) == 160
    c = Counter()
    for it in items:
        label, (kind, args) = it["label"], ask.match(it["question"])
        if "refuse" in label:
            c["refused ok" if kind is None else "refusal answered: " + it["question"]] += 1
        else:
            want = {k: v for k, v in label.items() if k != "kind"}
            if label["kind"] == "changes":
                want["vital"] = None  # the writer's label has the hours only; no question of the set names a vital there
            c["right" if (kind, args or {}) == (NAMES[label["kind"]], want) else "refused" if kind is None else "wrong kind"] += 1
    assert c == {"right": 67, "refused": 13, "refused ok": 79, "refusal answered: who acknowledged the alert": 1}
    card = " ".join((HERE / "card.md").read_text().split())
    for want in ("67 right, 13 refused, 0 to a wrong kind", "79 refused, 1 answered"):
        assert want in card, want

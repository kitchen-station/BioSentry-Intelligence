"""python -m research.agent <command>

    status                        the ledger and each proposal's state
    run [--rounds N]              N rounds (1 to 10, default 1) of the research loop
    ask "<question>"              a doctor's question, answered on simulated trials
    approve <id> [--by NAME]      a person signs off a proposal (writes only the ledger)
    apply <id>                    copy an approved proposal into the models folder
    export <file.json>            the questions, rounds and proposals as one JSON file (read only)

Exit 0 on success, 2 with one `refused: ...` line on any refusal.
"""

import re
import sys

from research.trials import TrialError, show

from . import ask as asking
from . import ledger, loop
from .config import AgentError, Config
from .planner import choose_planner


def _one_id(args: list[str], usage: str) -> str:
    if len(args) != 1:
        raise AgentError(f"usage: {usage}")
    return args[0]


def status(cfg: Config, args: list[str]) -> str:
    if args:
        raise AgentError("usage: status")
    records = ledger.read(cfg)
    lines = [f"ledger: {len(records)} records, chain ok"]
    finished = {r["id"] for r in records if r["type"] == "step"}
    for r in records:
        if r["type"] == "proposal":
            lines.append(f"{r['id']} {r['action']} {ledger.status(records, r['id'])} (step {r['step']})")
        elif r["type"] == "start" and r["id"] not in finished:
            lines.append(f"{r['id']} not finished")
    return "\n".join(lines)


def approve(cfg: Config, args: list[str]) -> str:
    by = None
    if len(args) == 3 and args[1] == "--by":
        args, by = args[:1], args[2]
    pid = _one_id(args, "approve <id> [--by NAME]")
    rec = ledger.approve(cfg, pid, by)
    return f"approved {pid} by {rec['by']}; not applied yet"


def apply(cfg: Config, args: list[str]) -> str:
    pid = _one_id(args, "apply <id>")
    rec = ledger.apply(cfg, pid)
    return f"applied {pid}: {len(rec['files'])} files"


def run(cfg: Config, args: list[str]) -> str:
    usage = "usage: run [--rounds N] (N from 1 to 10)"
    if args and not (len(args) == 2 and args[0] == "--rounds" and re.fullmatch(r"[0-9]{1,2}", args[1])
                     and 1 <= int(args[1]) <= 10):
        raise AgentError(usage)
    rounds = int(args[1]) if args else 1
    planner = choose_planner(cfg)
    for i in range(1, rounds + 1):
        print(f"round {i} of {rounds}", flush=True)
        lines = loop.run_round(cfg, planner)
        print("\n".join(lines), flush=True)
        if not any(x.startswith("step ") for x in lines):
            break  # the planner returned nothing
    return ""


def ask(cfg: Config, args: list[str]) -> str:
    return asking.ask(cfg, _one_id(args, 'ask "<question>"'))


def export(cfg: Config, args: list[str]) -> str:
    data = ledger.write_export(cfg, _one_id(args, "export <file.json>"))
    return (f"exported {len(data['questions'])} questions, {len(data['rounds'])} rounds, "
            f"{len(data['proposals'])} proposals")


COMMANDS = {"status": status, "run": run, "ask": ask, "approve": approve, "apply": apply, "export": export}


def main(argv: list[str], env=None) -> int:
    try:
        if not argv or argv[0] not in COMMANDS:
            raise AgentError(f"usage: python -m research.agent {'|'.join(COMMANDS)} ..." if not argv
                             else f"unknown command {show(argv[0])!r}")
        out = COMMANDS[argv[0]](Config.from_env(env), argv[1:])
    except (AgentError, TrialError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""python -m research.agent <command>

    status                        the ledger and each proposal's state
    approve <id> [--by NAME]      a person signs off a proposal (writes only the ledger)
    apply <id>                    copy an approved proposal into the models folder

Exit 0 on success, 2 with one `refused: ...` line on any refusal. `run` and `ask` are added to
COMMANDS by later slices.
"""

import sys

from research.trials import TrialError, show

from . import ledger
from .config import AgentError, Config


def _one_id(args: list[str], usage: str) -> str:
    if len(args) != 1:
        raise AgentError(f"usage: {usage}")
    return args[0]


def status(cfg: Config, args: list[str]) -> str:
    if args:
        raise AgentError("usage: status")
    records = ledger.read(cfg)
    lines = [f"ledger: {len(records)} records, chain ok"]
    for r in records:
        if r["type"] == "proposal":
            lines.append(f"{r['id']} {r['action']} {ledger.status(records, r['id'])} (step {r['step']})")
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


COMMANDS = {"status": status, "approve": approve, "apply": apply}


def main(argv: list[str], env=None) -> int:
    try:
        if not argv or argv[0] not in COMMANDS:
            raise AgentError(f"usage: python -m research.agent {'|'.join(COMMANDS)} ..." if not argv
                             else f"unknown command {show(argv[0])!r}")
        out = COMMANDS[argv[0]](Config.from_env(env), argv[1:])
    except (AgentError, TrialError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

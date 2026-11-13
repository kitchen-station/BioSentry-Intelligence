"""python -m research.patient_agent worker [--once]"""

import sys

from research.agent.config import AgentError

from . import worker


def main(argv: list[str], env=None) -> int:
    try:
        if not argv or argv[0] != "worker":
            raise AgentError("usage: python -m research.patient_agent worker [--once]")
        worker.run(argv[1:], env)
    except AgentError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

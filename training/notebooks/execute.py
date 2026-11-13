"""Run notebooks headless and save them with their outputs, in place.

    python training/notebooks/execute.py [reward_policy student_vitals confidence_check]   (from anywhere)

Needs the research environment (nbclient, ipykernel, matplotlib). A notebook only reads the run logs and
committed result files under models/ and training/runs/ and draws them. The kernel runs in this folder.
"""

import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).parent
NAMES = ("reward_policy", "student_vitals", "confidence_check")


def execute(path: Path, cwd: Path) -> None:
    book = nbformat.read(str(path), as_version=4)
    NotebookClient(book, kernel_name="python3", timeout=300, resources={"metadata": {"path": str(cwd)}}).execute()
    nbformat.write(book, str(path))


if __name__ == "__main__":
    for name in sys.argv[1:] or NAMES:
        execute(HERE / f"{name}.ipynb", HERE)
        print(f"{name}: executed")

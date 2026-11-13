"""Runs finetune.ipynb again, in place. Needs nbformat, nbclient and ipykernel (the optional environment's Python)."""

from pathlib import Path

import nbformat
from nbclient import NotebookClient

path = Path(__file__).with_name("finetune.ipynb")
nb = nbformat.read(path, as_version=4)
NotebookClient(nb, timeout=300, kernel_name="python3", record_timing=False,
               resources={"metadata": {"path": str(path.parent)}}).execute()
nbformat.write(nb, path)
print("executed", path.name)

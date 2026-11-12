"""The dataset folder must match its SOURCE.md before a single table is read."""

import hashlib
import re
from pathlib import Path

_ROW = re.compile(r"^\| (\S+) \| ([0-9a-f]{64}) \| (yes|NO) \|$")


class RealDataError(ValueError):
    """The one error this package raises. The message is a single line."""


def verify(folder: Path, needed: tuple[str, ...]) -> None:
    """Hash every file SOURCE.md lists and refuse on any difference, a 'NO' row or a missing table."""
    source = folder / "SOURCE.md"
    if not source.is_file():
        raise RealDataError("SOURCE.md: missing")
    listed: dict[str, str] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if m:
            if m.group(3) != "yes":
                raise RealDataError(f"SOURCE.md: {m.group(1)} is marked as not matching the official list")
            listed[m.group(1)] = m.group(2)
    if not listed:
        raise RealDataError("SOURCE.md: no checksum rows")
    for name in needed:
        if name not in listed:
            raise RealDataError(f"SOURCE.md: {name} is not listed")
    for name, want in sorted(listed.items()):
        path = folder / name
        if Path(name).is_absolute() or ".." in Path(name).parts or not path.is_file():
            raise RealDataError(f"{name}: missing or outside the folder")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        if h.hexdigest() != want:
            raise RealDataError(f"{name}: sha256 does not match SOURCE.md")

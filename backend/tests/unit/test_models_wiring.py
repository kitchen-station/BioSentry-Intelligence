"""The model code imports nothing but the standard library and biosentry, and is wired to nothing yet."""

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "biosentry"
NEW = [
    SRC / "domain" / "scoring" / "tier_c" / "plain_mlp.py",
    SRC / "domain" / "scoring" / "tier_c" / "float32.py",
    SRC / "domain" / "scoring" / "tier_c" / "confidence.py",
    SRC / "domain" / "scoring" / "tier_b" / "reward_policy.py",
    SRC / "services" / "models.py",
]
NEW_MODULES = {
    "biosentry.domain.scoring.tier_c",
    "biosentry.domain.scoring.tier_b.reward_policy",
    "biosentry.services.models",
}


def imported(path: Path) -> set[str]:
    """Every module name the file imports, relative imports resolved from its place in the package."""
    package = ".".join(path.relative_to(SRC.parent).with_suffix("").parts[:-1])
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join(parts + ([node.module] if node.module else []))
            names.add(base)
            names |= {f"{base}.{a.name}" for a in node.names}
    return names


def test_the_new_modules_import_only_the_standard_library_and_biosentry() -> None:
    for path in NEW:
        assert path.is_file()
        for name in imported(path):
            top = name.split(".")[0]
            assert top in sys.stdlib_module_names or top == "biosentry", f"{path.name} imports {name}"
        assert not [n for n in imported(path) if n.split(".")[0] == "research"]


def test_the_import_walk_sees_a_module_name_and_a_relative_import() -> None:
    assert "biosentry.domain.computed" in imported(SRC / "services" / "models.py")
    assert "biosentry.domain.scoring.tier_b.baseline" in imported(SRC / "domain" / "scoring" / "tier_b" / "reward_policy.py")
    assert any(n.startswith("biosentry") for n in imported(SRC / "api" / "main.py"))


def test_nothing_that_ranks_or_serves_imports_the_new_modules_yet() -> None:
    serving = list((SRC / "api").rglob("*.py")) + [
        SRC / "services" / "ward.py",
        SRC / "services" / "scoring.py",
        SRC / "domain" / "arbiter.py",
    ]
    assert len(serving) > 3
    for path in serving:
        for name in imported(path):
            assert not any(name == mod or name.startswith(mod + ".") for mod in NEW_MODULES), f"{path.name}: {name}"

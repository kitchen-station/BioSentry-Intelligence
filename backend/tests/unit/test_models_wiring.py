"""The model code imports nothing but the standard library and biosentry, and only the wiring imports it."""

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
    SRC / "domain" / "scoring" / "tier_c" / "features.py",
    SRC / "services" / "as_of.py",
    SRC / "services" / "model_signals.py",
]
NEW_MODULES = {
    "biosentry.domain.scoring.tier_c",
    "biosentry.domain.scoring.tier_b.reward_policy",
    "biosentry.services.models",
    "biosentry.services.as_of",
    "biosentry.services.model_signals",
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


def importers(module: str) -> set[str]:
    """Every file under src that imports this module (or one inside it), as a path inside biosentry."""
    return {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if any(n == module or n.startswith(module + ".") for n in imported(path))
    }


def test_only_the_wiring_imports_the_model_modules() -> None:
    assert importers("biosentry.services.model_signals") == {"services/ward.py"}
    assert importers("biosentry.services.models") == {
        "api/app.py",
        "services/model_signals.py",
        "services/ward.py",
    }
    assert importers("biosentry.services.as_of") == {"services/model_signals.py"}


def test_the_arbiter_and_the_ranking_code_only_receive_results() -> None:
    """The arbiter takes the confidence check's enum to type a result and nothing else of the model code;
    Tier A and `scoring.py` take no model module at all."""
    allowed = {"biosentry.domain.scoring.tier_c.confidence", "biosentry.domain.scoring.tier_c.confidence.Check"}  # `Check`: an enum, no code that computes
    ranking = [SRC / "domain" / "arbiter.py", SRC / "services" / "scoring.py", *(SRC / "domain" / "scoring" / "tier_a").rglob("*.py")]
    assert len(ranking) > 3
    for path in ranking:
        used = {n for n in imported(path) if any(n == m or n.startswith(m + ".") for m in NEW_MODULES)}
        assert used <= (allowed if path.name == "arbiter.py" else set()), f"{path.name}: {used}"
    assert "biosentry.domain.scoring.tier_c.confidence" in imported(SRC / "domain" / "arbiter.py")  # the walk sees it

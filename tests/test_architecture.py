"""Guards for architectural decisions that are easy to erode silently."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "maximusforge"
ADAPTER = SRC / "workflows" / "engines" / "langgraph_engine.py"

# Provider SDKs / backends that must never be imported by framework code (ADR-004).
FORBIDDEN_EVERYWHERE = {"openai", "anthropic", "ollama", "google"}


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_only_the_adapter_imports_langgraph() -> None:
    offenders = [
        p.relative_to(SRC)
        for p in SRC.rglob("*.py")
        if p != ADAPTER and "langgraph" in _imported_roots(p)
    ]
    assert offenders == []
    assert "langgraph" in _imported_roots(ADAPTER)


def test_no_vendor_llm_sdks_are_imported() -> None:
    offenders = [
        p.relative_to(SRC) for p in SRC.rglob("*.py") if _imported_roots(p) & FORBIDDEN_EVERYWHERE
    ]
    assert offenders == []


def test_importing_the_framework_does_not_load_langgraph() -> None:
    code = (
        "import sys, maximusforge.agents, maximusforge.workflows, maximusforge.llm, "
        "maximusforge.tools, maximusforge.memory, maximusforge.planning;"
        "sys.exit(1 if any(m.split('.')[0] == 'langgraph' for m in sys.modules) else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_importing_the_framework_needs_no_credentials() -> None:
    code = "import maximusforge.llm, maximusforge.agents"
    env = {"PATH": "", "PYTHONPATH": str(SRC.parent)}
    assert subprocess.run([sys.executable, "-c", code], env=env, check=False).returncode == 0

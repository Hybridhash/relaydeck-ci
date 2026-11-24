"""Find lazy exports, function-local imports and framework loading in package initializers."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

FLET_ROOTS = ("flet", "relaydeck_terminal", "relaydeck_notifications")

_PROBE = """
import importlib, json, sys
if sys.argv[1]:
    sys.path.insert(0, sys.argv[1])
importlib.import_module(sys.argv[2])
roots = tuple(sys.argv[3:])
tops = {name.split(".")[0] for name in sys.modules}
print(json.dumps(sorted(top for top in tops if top.startswith(roots))))
"""


def static_findings(init: Path) -> list[str]:
    tree = ast.parse(init.read_text(encoding="utf-8"))
    top = {id(node) for node in tree.body}
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if id(node) in top and node.name == "__getattr__":
                found.add(f"line {node.lineno}: lazy export (__getattr__)")
            for inner in ast.walk(node):
                if isinstance(inner, ast.Import | ast.ImportFrom):
                    found.add(f"line {inner.lineno}: import inside a function")
        elif isinstance(node, ast.Assign) and id(node) in top:
            if any(isinstance(t, ast.Name) and t.id == "__getattr__" for t in node.targets):
                found.add(f"line {node.lineno}: lazy export (__getattr__)")
    return sorted(found)


def fresh_process_findings(package: str, root: Path | None = None) -> list[str]:
    """Flet modules a fresh interpreter has loaded after importing `package`."""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _PROBE,
            str(root if root is not None else Path(__file__).resolve().parents[3] / "src"),
            package,
            *FLET_ROOTS,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [f"import failed: {result.stderr.strip().splitlines()[-1:]}"]
    return [f"loads {name}" for name in json.loads(result.stdout)]


def findings(init: Path, package: str, root: Path | None = None) -> list[str]:
    return static_findings(init) + fresh_process_findings(package, root)


def discover(src: Path, prefixes: tuple[str, ...]) -> dict[str, Path]:
    """{dotted package: __init__.py} for the existing packages under the named prefixes."""
    found: dict[str, Path] = {}
    for init in sorted(src.rglob("__init__.py")):
        if "__pycache__" in init.parts:
            continue
        package = ".".join(init.parent.relative_to(src.parent).parts)
        if any(package == p or package.startswith(p + ".") for p in prefixes):
            found[package] = init
    return found

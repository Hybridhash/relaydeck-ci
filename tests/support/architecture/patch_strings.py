"""Dotted patch targets in tests resolve to something that exists.

`monkeypatch.setattr("relaydeck.x.y.name", ...)` and `mock.patch("relaydeck.x.y.name")` name a
module path in a string, so a move that forgets the string fails only when that test runs, or
not at all with `raising=False`. This reads the sources, never imports them: a target resolves
when its module file exists and the name is defined, assigned or imported at the module's top
level (and, for classes, in the class body). Past an imported name or an assigned value the chain
cannot be followed statically and is accepted.
"""

from __future__ import annotations

import ast
from pathlib import Path

PATCH_FUNCTIONS = {"setattr", "delattr", "patch"}


def patch_targets(source: str) -> list[tuple[int, str]]:
    """(line, dotted target) for every call that patches by a dotted string."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "dict" and isinstance(func, ast.Attribute):
            owner = func.value
            if getattr(owner, "attr", getattr(owner, "id", "")) != "patch":
                continue
        elif name not in PATCH_FUNCTIONS:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and "." in first.value:
            found.append((node.lineno, first.value))
    return found


def _bindings(body: list[ast.stmt]) -> dict[str, ast.AST | None]:
    """Names bound at one level: a class node, or None for anything we cannot look into."""
    out: dict[str, ast.AST | None] = {}
    for node in body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = node
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out[node.name] = None
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for leaf in ast.walk(target):
                    if isinstance(leaf, ast.Name):
                        out.setdefault(leaf.id, None)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign) and isinstance(node.target, ast.Name):
            out.setdefault(node.target.id, None)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.setdefault((alias.asname or alias.name).split(".")[0], None)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.setdefault(alias.asname or alias.name, None)
        elif isinstance(node, ast.If | ast.Try | ast.With | ast.For | ast.While):
            for attr in ("body", "orelse", "finalbody"):
                out.update(
                    {
                        k: v
                        for k, v in _bindings(getattr(node, attr, []) or []).items()
                        if k not in out
                    }
                )
            for handler in getattr(node, "handlers", []) or []:
                out.update({k: v for k, v in _bindings(handler.body).items() if k not in out})
    return out


def resolve(dotted: str, roots: dict[str, Path]) -> str | None:
    """None when `dotted` resolves (or is not ours), else why it does not."""
    parts = dotted.split(".")
    base = roots.get(parts[0])
    if base is None:
        return None
    current, index, module = base, 1, base / "__init__.py"
    while index < len(parts):
        directory, file = current / parts[index], current / f"{parts[index]}.py"
        if file.exists():
            module, index = file, index + 1
            break
        if directory.is_dir():
            current, module, index = directory, directory / "__init__.py", index + 1
            continue
        break
    if not module.exists():
        return f"no module for {'.'.join(parts[:index])}"
    rest = parts[index:]
    if not rest:
        return None
    scope = _bindings(ast.parse(module.read_text(encoding="utf-8")).body)
    for position, name in enumerate(rest):
        if name not in scope:
            return f"{module.name} has no {'.'.join(rest[: position + 1])}"
        node = scope[name]
        if not isinstance(node, ast.ClassDef):
            return None
        scope = _bindings(node.body)
    return None

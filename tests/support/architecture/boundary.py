"""Direct-import allowlist for a package, found by walking its files.

Import-linter contracts list forbidden targets, so a module nobody listed slips through. This
check goes the other way: every direct import of the checked package that resolves inside the
project's own top-level package must land in an allowed prefix. Standard-library and framework
imports are not looked at.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _exists(root: Path, module: str) -> bool:
    base = root.joinpath(*module.split("."))
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def project_imports(path: Path, root: Path) -> set[tuple[int, str]]:
    """(line, dotted module) for each project import in the file, anywhere in its tree.

    `from pkg import name` resolves to `pkg.name` when that is a module of the project and to
    `pkg` otherwise; relative imports are resolved against the file's own package.
    """
    top = root.name
    package = _module_name(path, root.parent).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    found: set[tuple[int, str]] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update((node.lineno, a.name) for a in node.names if a.name.split(".")[0] == top)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            if module.split(".")[0] != top:
                continue
            for alias in node.names:
                sub = f"{module}.{alias.name}"
                found.add((node.lineno, sub if _exists(root.parent, sub) else module))
    return found


def boundary_violations(root: Path, package: str, allowed: tuple[str, ...]) -> list[str]:
    """Imports in `package` (a dotted name under `root.parent`) that leave `allowed`.

    `allowed` holds dotted prefixes; the package itself is always allowed.
    """
    base = root.parent
    folder = base.joinpath(*package.split("."))
    prefixes = (package, *allowed)
    out: list[str] = []
    for path in sorted(folder.rglob("*.py")):
        for line, module in sorted(project_imports(path, root)):
            if not any(module == p or module.startswith(p + ".") for p in prefixes):
                out.append(f"{path.relative_to(base)}:{line}: imports {module}")
    return out


def python_modules(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.py") if "__pycache__" not in p.parts)

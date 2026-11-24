"""Check public feature and screen entry imports without importing the application."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from tests.support.architecture.entry_bindings import entry_attribute_violations


def _exports(client: Path) -> dict[str, dict[str, set[str]]]:
    found: dict[str, dict[str, set[str]]] = {}
    for feature in (client / "features").iterdir():
        for entry in ("api", "ui"):
            path = feature / f"{entry}.py"
            if not path.is_file():
                continue
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in node.targets
                ):
                    found.setdefault(feature.name, {})[entry] = set(ast.literal_eval(node.value))
    return found


def entry_violations(client: Path, prefix: str = "relaydeck.client") -> list[str]:
    """Outside consumers use declared entries of published features and screens."""
    public = _exports(client)
    failures: list[str] = []
    features = prefix + ".features."
    screens = prefix + ".screens."
    for path in client.rglob("*.py"):
        relative = path.relative_to(client)
        layer = relative.parts[0]
        own_screen = relative.parts[1] if layer == "screens" and len(relative.parts) > 2 else ""
        own_feature = relative.parts[1] if layer == "features" and len(relative.parts) > 2 else ""
        package = prefix + "." + ".".join(relative.with_suffix("").parts)
        package = (
            package.removesuffix(".__init__")
            if path.name == "__init__.py"
            else package.rsplit(".", 1)[0]
        )
        source = path.read_text()
        tree = ast.parse(source)
        imports: dict[ast.alias, set[str]] = {}

        def check(
            module: str,
            member: str | None,
            alias: ast.alias,
            line: int,
            *,
            own_screen: str = own_screen,
            own_feature: str = own_feature,
            relative: Path = relative,
            layer: str = layer,
            imports: dict[ast.alias, set[str]] = imports,
        ) -> None:
            if module.startswith(screens):
                parts = module[len(screens) :].split(".")
                if parts[0] != own_screen:
                    entry = parts[1:] if len(parts) > 1 else [member] if member else []
                    if entry != ["screen"]:
                        failures.append(
                            f"{relative}:{line}: screen import bypasses screen.py: {module}"
                        )
            if not module.startswith(features) or layer not in {"screens", "widgets", "features"}:
                return
            parts = module[len(features) :].split(".")
            feature = parts[0]
            if feature == own_feature or (layer != "screens" and feature not in public):
                return
            rest = parts[1:]
            if not rest and member in {"api", "ui"}:
                rest, member = [member], None
            if len(rest) != 1 or rest[0] not in {"api", "ui"}:
                failures.append(f"{relative}:{line}: private feature import: {module}")
                return
            names = public.get(feature, {}).get(rest[0], set())
            if member is not None and member not in names:
                failures.append(f"{relative}:{line}: undeclared feature name: {module}.{member}")
            if member is None:
                imports[alias] = names

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    check(alias.name, None, alias, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = importlib.util.resolve_name("." * node.level + module, package)
                for alias in node.names:
                    if module in {prefix + ".features", prefix + ".screens"}:
                        check(module + "." + alias.name, None, alias, node.lineno)
                    else:
                        check(module, alias.name, alias, node.lineno)
        for line, attribute in entry_attribute_violations(source, imports, tree):
            failures.append(f"{relative}:{line}: undeclared entry attribute: {attribute}")
    return failures

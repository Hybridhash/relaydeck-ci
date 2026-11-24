"""Resolve imported entry modules in lexical scopes and statement order."""

from __future__ import annotations

import ast
import symtable
from dataclasses import dataclass, field


@dataclass
class _Scope:
    table: symtable.SymbolTable
    aliases: dict[str, set[str] | None] = field(default_factory=dict)
    children: list[symtable.SymbolTable] = field(init=False)
    inlined: bool = False
    globals: set[str] = field(init=False)
    history: dict[str, set[str] | None] = field(init=False)

    def __post_init__(self) -> None:
        self.children = self.table.get_children()
        self.globals = {s.get_name() for s in self.table.get_symbols() if s.is_global()}
        if self.table.get_type() == "function" and not self.inlined:
            self.aliases = {s.get_name(): None for s in self.table.get_symbols() if s.is_local()}
        self.history = dict(self.aliases)


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (parent := _dotted(node.value)):
        return parent + "." + node.attr
    return None


class _EntryUses(ast.NodeVisitor):
    def __init__(self, source: str, imports: dict[ast.alias, set[str]]) -> None:
        self.scopes = [_Scope(symtable.symtable(source, "<entry-consumer>", "exec"))]
        self.imports = imports
        self.failures: list[tuple[int, str]] = []
        self.pending: list[tuple[list[_Scope], list[ast.AST]]] = []

    def _lookup(self, name: str | None) -> set[str] | None:
        if name is None:
            return None
        root = name.split(".", 1)[0]
        current = self.scopes[-1]
        deferred = False
        for scope in reversed(self.scopes):
            if scope is not current and scope.table.get_type() == "class":
                continue
            aliases = scope.history if deferred else scope.aliases
            if name in aliases:
                return aliases[name]
            if root in aliases:
                return None
            if scope.table.get_type() == "function" and not scope.inlined:
                if root in scope.globals:
                    return self.scopes[0].history.get(name)
                deferred = True
        return None

    def _bind(self, name: str, allowed: set[str] | None, *, package: bool = False) -> None:
        scope = self.scopes[-1]
        aliases = scope.aliases
        root = name.split(".", 1)[0]
        if package:
            for parent in self.scopes[:-1]:
                for key, names in parent.history.items():
                    if key.startswith(root + "."):
                        aliases.setdefault(key, names)
        if not package:
            for key in list(aliases):
                if key.split(".", 1)[0] == root:
                    del aliases[key]
        aliases.setdefault(root, None)
        aliases[name] = allowed
        scope.history.setdefault(root, None)
        for key, names in aliases.items():
            if names is not None:
                previous = scope.history.get(key)
                scope.history[key] = names if previous is None else previous & names

    def _target(self, node: ast.AST, allowed: set[str] | None = None) -> None:
        if isinstance(node, ast.Name):
            self._bind(node.id, allowed)
        elif isinstance(node, ast.Tuple | ast.List):
            for item in node.elts:
                self._target(item)
        elif isinstance(node, ast.Starred):
            self._target(node.value)
        else:
            self.visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        binding = _dotted(node.value)
        allowed = self._lookup(binding)
        if allowed is not None and node.attr not in allowed:
            self.failures.append((node.lineno, f"{binding}.{node.attr}"))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind(
                alias.asname or alias.name, self.imports.get(alias), package=not alias.asname
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._bind(alias.asname or alias.name, self.imports.get(alias))

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        allowed = self._lookup(_dotted(node.value))
        for target in node.targets:
            self._target(target, allowed)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._target(node.target, self._lookup(_dotted(node.value)))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        self._target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._target(node.target, self._lookup(_dotted(node.value)))

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._target(target)

    def _body(self, body: list[ast.AST]) -> None:
        for statement in body:
            self.visit(statement)

    def _enter(self, node: ast.AST, name: str) -> None:
        parent = self.scopes[-1]
        child = next(
            c for c in parent.children if c.get_name() == name and c.get_lineno() == node.lineno
        )
        parent.children.remove(child)
        self.scopes.append(_Scope(child))

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for value in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
            if value is not None:
                self.visit(value)
        for arg in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *([node.args.vararg] if node.args.vararg else []),
            *([node.args.kwarg] if node.args.kwarg else []),
        ]:
            if arg.annotation is not None:
                self.visit(arg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        self._bind(node.name, None)
        self._enter(node, node.name)
        self.pending.append((list(self.scopes), node.body))
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for value in [*node.args.defaults, *node.args.kw_defaults]:
            if value is not None:
                self.visit(value)
        self._enter(node, "lambda")
        self.pending.append((list(self.scopes), [node.body]))
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for value in [*node.decorator_list, *node.bases, *(kw.value for kw in node.keywords)]:
            self.visit(value)
        self._bind(node.name, None)
        self._enter(node, node.name)
        self._body(node.body)
        self.scopes.pop()

    def _branches(self, bodies: list[list[ast.stmt]], target: ast.expr | None = None) -> None:
        scope = self.scopes[-1]
        before = dict(scope.aliases)
        outcomes = []
        for index, body in enumerate(bodies):
            scope.aliases = dict(before)
            if index == 0 and target is not None:
                self._target(target)
            self._body(body)
            outcomes.append(scope.aliases)
        scope.aliases = self._merged(outcomes)

    @staticmethod
    def _merged(outcomes: list[dict[str, set[str] | None]]) -> dict[str, set[str] | None]:
        merged: dict[str, set[str] | None] = {}
        for key in set().union(*(outcome.keys() for outcome in outcomes)):
            options = [outcome[key] for outcome in outcomes if outcome.get(key) is not None]
            merged[key] = set.intersection(*options) if options else None
        return merged

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._branches([node.body, node.orelse])

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        self.visit(node.body)
        self.visit(node.orelse)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._branches([node.body, []], target=node.target)
        self._body(node.orelse)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._branches([node.body, []])
        self._body(node.orelse)

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._target(item.optional_vars)
        self._body(node.body)

    visit_AsyncWith = visit_With

    def visit_Try(self, node: ast.Try | ast.TryStar) -> None:
        scope = self.scopes[-1]
        prefixes = [dict(scope.aliases)]
        for statement in node.body:
            self.visit(statement)
            prefixes.append(dict(scope.aliases))
        self._body(node.orelse)
        outcomes = [dict(scope.aliases)]
        for handler in node.handlers:
            scope.aliases = self._merged(prefixes)
            self.visit(handler)
            outcomes.append(dict(scope.aliases))
        scope.aliases = self._merged(outcomes)
        self._body(node.finalbody)

    visit_TryStar = visit_Try

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._bind(node.name, None)
        self._body(node.body)
        if node.name:
            self._bind(node.name, None)

    def _comprehension(self, node: ast.AST, name: str, values: list[ast.expr]) -> None:
        self.visit(node.generators[0].iter)
        parent = self.scopes[-1]
        if name == "genexpr":
            self._enter(node, name)
            body = [
                item
                for index, generator in enumerate(node.generators)
                for item in ([generator.iter] if index else []) + generator.ifs
            ]
            self.pending.append((list(self.scopes), body + values))
            self.scopes.pop()
            return
        else:
            # Eager comprehensions are inlined in Python 3.13, with isolated iteration names.
            scope = _Scope(parent.table, inlined=True)
            scope.children = parent.children
            self.scopes.append(scope)
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            self._target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self.scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._comprehension(node, "listcomp", [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._comprehension(node, "setcomp", [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._comprehension(node, "dictcomp", [node.key, node.value])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._comprehension(node, "genexpr", [node.elt])


def entry_attribute_violations(
    source: str, imports: dict[ast.alias, set[str]], tree: ast.Module
) -> list[tuple[int, str]]:
    visitor = _EntryUses(source, imports)
    visitor.visit(tree)
    while visitor.pending:
        scopes, body = visitor.pending.pop(0)
        visitor.scopes = scopes
        visitor._body(body)
    return visitor.failures

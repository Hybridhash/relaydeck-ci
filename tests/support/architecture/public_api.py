"""Check direct imports of a context against its public Python entry."""

from pathlib import Path

from tests.support.architecture.boundary import project_imports


def entry_violations(
    root: Path, package: str, exceptions: frozenset[tuple[str, str]] = frozenset()
) -> list[str]:
    owner = root.parent.joinpath(*package.split("."))
    findings = []
    for path in sorted(root.rglob("*.py")):
        if path.is_relative_to(owner):
            continue
        relative = path.relative_to(root).as_posix()
        for line, module in sorted(project_imports(path, root)):
            if module == package or module.startswith(package + "."):
                if module != package + ".api" and (relative, module) not in exceptions:
                    findings.append(f"{relative}:{line}: imports {module}")
    return findings

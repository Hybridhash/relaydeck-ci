"""The gateway consumes the read-service port and stable results, not host internals."""

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "relative",
    [
        "gateway/http/git.py",
        "gateway/http/git_graph.py",
        "gateway/http/git_access.py",
        "git/results.py",
    ],
)
def test_git_read_boundary_excludes_native_implementation_dependencies(relative: str) -> None:
    source = Path(__file__).parents[2] / "src/relaydeck" / relative
    allowed = {"relaydeck.git.api"}
    for node in ast.walk(ast.parse(source.read_text())):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("relaydeck.git")
        ):
            assert node.module in allowed
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("relaydeck.git"):
                    assert alias.name in allowed

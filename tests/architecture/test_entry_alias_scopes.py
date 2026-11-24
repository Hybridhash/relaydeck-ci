"""Entry-module aliases keep lexical scope, rebinding order and branch provenance."""

from pathlib import Path

import pytest

from tests.support.architecture.entries import entry_violations

CASES = [
    (
        "def bad():\n"
        " from fxclient.features.alpha import api as entry\n"
        " return entry.private\n"
        "def good():\n"
        " from fxclient.features.beta import api as entry\n"
        " return entry.private",
        True,
    ),
    (
        "def good():\n"
        " from fxclient.features.beta import api as entry\n"
        " return entry.private\n"
        "def bad():\n"
        " from fxclient.features.alpha import api as entry\n"
        " return entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def local(entry):\n"
        " return entry.private\n"
        "value = entry.Value",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def local(*, entry):\n"
        " return entry.private",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def local(*entry):\n"
        " return entry.private",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def local(**entry):\n"
        " return entry.private",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def outer():\n"
        " def inner():\n"
        "  return entry.private\n"
        " return inner",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def outer(entry):\n"
        " def inner():\n"
        "  return entry.private\n"
        " return inner",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def local():\n"
        " entry = object()\n"
        " return entry.private",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "value = entry.private\n"
        "from fxclient.features.beta import api as entry\n"
        "other = entry.private",
        True,
    ),
    (
        "from fxclient.features.beta import api as entry\n"
        "value = entry.private\n"
        "from fxclient.features.alpha import api as entry\n"
        "other = entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "import unrelated as entry\n"
        "value = entry.private",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "other = entry\n"
        "from fxclient.features.beta import api as entry\n"
        "value = other.private",
        True,
    ),
    (
        "def outer():\n"
        " from fxclient.features.alpha import api as entry\n"
        " def bad():\n"
        "  return entry.private\n"
        " def good():\n"
        "  from fxclient.features.beta import api as entry\n"
        "  return entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "class Local:\n"
        " from fxclient.features.beta import api as entry\n"
        " def bad(self):\n"
        "  return entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "class Local:\n"
        " value = entry.private\n"
        " from fxclient.features.beta import api as entry",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\nvalue = lambda entry: entry.private",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\nvalue = lambda: entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "value = [entry.private for entry in objects]",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "value = [entry.private for item in objects]",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "value = (entry.private for entry in objects)",
        False,
    ),
    (
        "if condition:\n"
        " from fxclient.features.alpha import api as entry\n"
        "else:\n"
        " from fxclient.features.beta import api as entry\n"
        "value = entry.private",
        True,
    ),
    (
        "try:\n"
        " from fxclient.features.alpha import api as entry\n"
        "except ImportError:\n"
        " from fxclient.features.beta import api as entry\n"
        "value = entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def outer():\n"
        " from fxclient.features.beta import api as entry\n"
        " def bad():\n"
        "  global entry\n"
        "  return entry.private",
        True,
    ),
    (
        "from fxclient.features.beta import api as entry\n"
        "def outer():\n"
        " from fxclient.features.alpha import api as entry\n"
        " def bad():\n"
        "  nonlocal entry\n"
        "  return entry.private",
        True,
    ),
    (
        "def outer():\n"
        " if TYPE_CHECKING:\n"
        "  from fxclient.features.alpha import api as entry\n"
        " def bad():\n"
        "  return entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def local():\n"
        " def inner(value: entry.private):\n"
        "  pass",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "for entry in objects:\n"
        " value = entry.private\n"
        "value = entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "for entry in objects:\n"
        " value = entry.private\n"
        "value = entry.Value",
        False,
    ),
    (
        "def bad():\n"
        " return entry.private\n"
        "from fxclient.features.alpha import api as entry\n"
        "bad()",
        True,
    ),
    (
        "def outer():\n"
        " def bad():\n"
        "  return entry.private\n"
        " from fxclient.features.alpha import api as entry\n"
        " return bad()\n"
        "outer()",
        True,
    ),
    (
        "from fxclient.features.beta import api as entry\n"
        "def bad():\n"
        " return entry.private\n"
        "from fxclient.features.alpha import api as entry\n"
        "bad()",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def bad():\n"
        " return entry.private\n"
        "from fxclient.features.beta import api as entry\n"
        "bad()",
        True,
    ),
    (
        "try:\n"
        " from fxclient.features.alpha import api as entry\n"
        " raise RuntimeError()\n"
        "except RuntimeError:\n"
        " result = entry.private",
        True,
    ),
    (
        "try:\n"
        " from fxclient.features.alpha import api as entry\n"
        " raise RuntimeError()\n"
        "except RuntimeError:\n"
        " result = entry.Value",
        False,
    ),
    (
        "import fxclient.features.alpha.api\n"
        "def bad():\n"
        " import fxclient.features.beta.api\n"
        " return fxclient.features.alpha.api.private",
        True,
    ),
    (
        "import fxclient.features.alpha.api\n"
        "def good():\n"
        " import fxclient.features.beta.api\n"
        " return fxclient.features.alpha.api.Value",
        False,
    ),
    (
        "def good():\n"
        " return entry.private\n"
        "from fxclient.features.beta import api as entry\n"
        "good()",
        False,
    ),
    (
        "def outer():\n"
        " def good():\n"
        "  return entry.private\n"
        " from fxclient.features.beta import api as entry\n"
        " return good()\n"
        "outer()",
        False,
    ),
    (
        "def local(entry):\n"
        " return entry.private\n"
        "from fxclient.features.alpha import api as entry",
        False,
    ),
    (
        "try:\n"
        " from fxclient.features.alpha import api as entry\n"
        " raise RuntimeError()\n"
        "except RuntimeError as entry:\n"
        " result = entry.private",
        False,
    ),
    (
        "bad = lambda: entry.private\nfrom fxclient.features.alpha import api as entry\nbad()",
        True,
    ),
    (
        "from fxclient.features.beta import api as entry\n"
        "bad = lambda: entry.private\n"
        "from fxclient.features.alpha import api as entry\n"
        "bad()",
        True,
    ),
    (
        "def outer():\n"
        " bad = lambda: entry.private\n"
        " from fxclient.features.alpha import api as entry\n"
        " return bad()",
        True,
    ),
    (
        "from fxclient.features.beta import api as entry\n"
        "try:\n"
        " from fxclient.features.alpha import api as entry\n"
        " raise RuntimeError()\n"
        "except RuntimeError:\n"
        " result = entry.private",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "try:\n"
        " from fxclient.features.beta import api as entry\n"
        "except ImportError:\n"
        " result = entry.private",
        True,
    ),
    (
        "import fxclient.features.alpha.api\n"
        "def local(fxclient):\n"
        " return fxclient.features.alpha.api.private",
        False,
    ),
    (
        "import fxclient.features.alpha.api\n"
        "def local():\n"
        " fxclient = object()\n"
        " return fxclient.features.alpha.api.private",
        False,
    ),
    (
        "bad = (entry.private for item in items)\n"
        "from fxclient.features.alpha import api as entry\n"
        "list(bad)",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def bad():\n"
        " class Inner:\n"
        "  value = entry.private\n"
        " return Inner.value\n"
        "result = bad()\n"
        "from fxclient.features.beta import api as entry",
        True,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def local():\n"
        " class Inner:\n"
        "  entry = object()\n"
        "  value = entry.private\n"
        "from fxclient.features.beta import api as entry",
        False,
    ),
    (
        "from fxclient.features.beta import api as entry\n"
        "class Immediate:\n"
        " value = entry.private\n"
        "from fxclient.features.alpha import api as entry",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "def local(entry):\n"
        " class Inner:\n"
        "  value = entry.private\n"
        "from fxclient.features.beta import api as entry",
        False,
    ),
    (
        "from fxclient.features.alpha import api as entry\n"
        "class Immediate:\n"
        " value = entry.private\n"
        "from fxclient.features.beta import api as entry",
        True,
    ),
    (
        "class Ordered:\n"
        " from fxclient.features.beta import api as entry\n"
        " value = entry.private\n"
        " from fxclient.features.alpha import api as entry\n"
        " other = entry.private",
        True,
    ),
]


@pytest.mark.parametrize(
    "consumer", ["screens/a/screen.py", "widgets/block.py", "features/gamma/owner.py"]
)
@pytest.mark.parametrize("type_only", [False, True])
@pytest.mark.parametrize(("source", "refused"), CASES)
def test_entry_alias_scopes_and_statement_order(
    tmp_path: Path, consumer: str, type_only: bool, source: str, refused: bool
) -> None:
    if type_only:
        source = "if TYPE_CHECKING:\n" + "".join(" " + line + "\n" for line in source.splitlines())
    source = (
        "from __future__ import annotations\nfrom typing import TYPE_CHECKING\n" + source + "\n"
    )
    client = tmp_path / "client"
    for name, text in {
        "features/alpha/api.py": '__all__ = ["Value", "choose"]\n',
        "features/beta/api.py": '__all__ = ["private"]\n',
        consumer: source,
    }.items():
        path = client / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    failures = entry_violations(client, "fxclient")
    assert bool(failures) is refused, failures

"""The web shell size record and budget check in scripts/build_manifest.py.

Synthetic output trees only: raw and gzip totals over the shell set, the
growth limit against a previous manifest, and the no-baseline first build.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "rd_build_manifest", ROOT / "scripts" / "build_manifest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bm = _module()


def _tree(root: Path, *, size: int) -> Path:
    """A built tree whose shell members all have `size` bytes of distinct content."""
    for index, name in enumerate(bm._shell_member_names()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([index % 251]) * size)
    (root / "unrelated.bin").write_bytes(b"x" * 9999)  # never in the shell set
    return root


def _manifest(path: Path, tree: Path) -> Path:
    doc = {"web_output": {"shell_sizes": bm.shell_sizes(tree)}}
    path.write_text(json.dumps(doc))
    return path


def test_shell_sizes_record_raw_and_gzip_per_member(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "out", size=1000)
    sizes = bm.shell_sizes(tree)
    members = bm._shell_member_names()
    assert set(sizes["files"]) == set(members)
    assert sizes["total_bytes"] == 1000 * len(members)
    entry = sizes["files"]["main.dart.js"]
    assert entry["bytes"] == 1000
    assert entry["gzip_bytes"] == len(gzip.compress((tree / "main.dart.js").read_bytes(), mtime=0))
    assert 0 < sizes["total_gzip_bytes"] < sizes["total_bytes"]


def test_shell_sizes_skip_missing_members(tmp_path: Path) -> None:
    root = tmp_path / "out"
    (root / "canvaskit").mkdir(parents=True)
    (root / "canvaskit" / "canvaskit.wasm").write_bytes(b"wasm")
    sizes = bm.shell_sizes(root)
    assert set(sizes["files"]) == {"canvaskit/canvaskit.wasm"}
    assert sizes["total_bytes"] == 4


def test_budget_passes_within_the_growth_limit(tmp_path: Path) -> None:
    previous = _manifest(tmp_path / "prev.json", _tree(tmp_path / "a", size=1000))
    outcome = bm.shell_budget_check(str(previous), str(_tree(tmp_path / "b", size=1040)))
    assert outcome["baseline"] is True and outcome["ok"] is True
    assert outcome["total_bytes"]["growth_pct"] == pytest.approx(4.0)


def test_budget_fails_above_the_growth_limit(tmp_path: Path) -> None:
    previous = _manifest(tmp_path / "prev.json", _tree(tmp_path / "a", size=1000))
    outcome = bm.shell_budget_check(str(previous), str(_tree(tmp_path / "b", size=1200)))
    assert outcome["ok"] is False
    assert outcome["total_bytes"]["growth_pct"] > bm.SHELL_BUDGET_MAX_GROWTH_PCT


def test_budget_allows_shrinkage(tmp_path: Path) -> None:
    previous = _manifest(tmp_path / "prev.json", _tree(tmp_path / "a", size=1000))
    outcome = bm.shell_budget_check(str(previous), str(_tree(tmp_path / "b", size=500)))
    assert outcome["ok"] is True


def test_budget_without_a_recorded_baseline_passes_and_says_so(tmp_path: Path) -> None:
    # Manifests written before the size record existed carry no baseline.
    previous = tmp_path / "old.json"
    previous.write_text(json.dumps({"web_output": {"file_count": 3}}))
    outcome = bm.shell_budget_check(str(previous), str(_tree(tmp_path / "b", size=5000)))
    assert outcome["baseline"] is False and outcome["ok"] is True


def test_budget_cli_exit_codes(tmp_path: Path) -> None:
    previous = _manifest(tmp_path / "prev.json", _tree(tmp_path / "a", size=1000))
    grown = _tree(tmp_path / "b", size=2000)
    assert bm.main(["--check-shell-budget", str(previous), "--web-out", str(grown)]) == 1
    same = _tree(tmp_path / "c", size=1000)
    assert bm.main(["--check-shell-budget", str(previous), "--web-out", str(same)]) == 0
    assert bm.main(["--check-shell-budget", str(previous)]) == 2  # --web-out is required
    assert (
        bm.main(["--check-shell-budget", str(tmp_path / "nope.json"), "--web-out", str(same)]) == 1
    )


def test_web_output_record_carries_shell_sizes(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "out", size=100)
    record = bm.web_output_record(str(tree))
    sizes = record["shell_sizes"]
    assert isinstance(sizes, dict)
    assert sizes["total_bytes"] == 100 * len(bm._shell_member_names())

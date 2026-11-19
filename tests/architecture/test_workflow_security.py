"""Workflow actions are immutable and checkout tokens have minimal access."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
PINNED_ACTION = re.compile(r"[^@\s]+@[0-9a-fA-F]{40}")
# Credential exceptions name only jobs that push repository changes.
PUSHING_JOBS: dict[str, frozenset[str]] = {}
# Write exceptions identify the workflow filename, job and permission scope.
# Each entry must explain why that specific job needs the grant.
ALLOWED_JOB_WRITES: dict[tuple[str, str, str], str] = {}
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
VALID_WORKFLOW = f"""\
permissions:
  contents: read
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: {CHECKOUT}
        with:
          persist-credentials: false
"""


def _findings(
    source: str, *, workflow_name: str = "", pushing_jobs: frozenset[str] = frozenset()
) -> list[str]:
    workflow = yaml.safe_load(source)
    findings = []
    if workflow.get("permissions") != {"contents": "read"}:
        findings.append("top-level permissions must be contents: read")
    for job_name, job in workflow["jobs"].items():
        permissions = job.get("permissions", {})
        if permissions == "write-all":
            findings.append(f"{job_name}: job-level permissions must not be write-all")
        elif isinstance(permissions, dict):
            for scope, access in permissions.items():
                reason = ALLOWED_JOB_WRITES.get((workflow_name, job_name, scope), "")
                if access == "write" and not reason.strip():
                    findings.append(f"{job_name}: unauthorized job-level write permission: {scope}")
        # Reusable workflows carry uses at job level instead of inside steps.
        for entry in [job, *job.get("steps", [])]:
            action = entry.get("uses")
            if action is None or action.startswith("./"):
                continue
            if not PINNED_ACTION.fullmatch(action):
                findings.append(f"{job_name}: unpinned uses: {action}")
            if action.split("@", 1)[0].lower() == "actions/checkout":
                persist = entry.get("with", {}).get("persist-credentials")
                if job_name not in pushing_jobs and persist is not False and persist != "false":
                    findings.append(f"{job_name}: checkout must set persist-credentials: false")
    return findings


@pytest.mark.parametrize(
    "path", sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]), ids=lambda p: p.name
)
def test_workflow_security(path: Path) -> None:
    assert (
        _findings(
            path.read_text(encoding="utf-8"),
            workflow_name=path.name,
            pushing_jobs=PUSHING_JOBS.get(path.name, frozenset()),
        )
        == []
    )


@pytest.mark.parametrize("ref", ["v7.0.1", "main", "3d3c42e", "g" * 40, "a" * 39, "a" * 41])
def test_unpinned_action_is_rejected(ref: str) -> None:
    source = VALID_WORKFLOW.replace(CHECKOUT, f"actions/checkout@{ref}")
    assert _findings(source) == [f"checks: unpinned uses: actions/checkout@{ref}"]


def test_unpinned_reusable_workflow_is_rejected() -> None:
    source = """\
permissions:
  contents: read
jobs:
  checks:
    uses: example/project/.github/workflows/checks.yml@main
"""
    assert _findings(source) == [
        "checks: unpinned uses: example/project/.github/workflows/checks.yml@main"
    ]


@pytest.mark.parametrize(
    "permissions", ["", "permissions: {}\n", "permissions: read-all\n", "permissions: write-all\n"]
)
def test_missing_or_broad_top_level_permissions_are_rejected(permissions: str) -> None:
    source = VALID_WORKFLOW.replace("permissions:\n  contents: read\n", permissions)
    assert _findings(source) == ["top-level permissions must be contents: read"]


def test_job_permissions_do_not_replace_top_level_permissions() -> None:
    source = VALID_WORKFLOW.replace("permissions:\n  contents: read\n", "").replace(
        "  checks:\n", "  checks:\n    permissions:\n      contents: read\n"
    )
    assert _findings(source) == ["top-level permissions must be contents: read"]


@pytest.mark.parametrize(
    "setting", ["", "        with: {}\n", "        with:\n          persist-credentials: true\n"]
)
def test_persisted_checkout_credentials_are_rejected(setting: str) -> None:
    source = VALID_WORKFLOW.replace(
        "        with:\n          persist-credentials: false\n", setting
    )
    assert _findings(source) == ["checks: checkout must set persist-credentials: false"]


def test_write_permissions_alone_do_not_exempt_checkout() -> None:
    source = VALID_WORKFLOW.replace(
        "          persist-credentials: false", "          fetch-depth: 0"
    )
    source = source.replace("  checks:\n", "  checks:\n    permissions:\n      contents: write\n")
    assert _findings(source) == [
        "checks: unauthorized job-level write permission: contents",
        "checks: checkout must set persist-credentials: false",
    ]


def test_explicit_pushing_job_can_keep_checkout_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(ALLOWED_JOB_WRITES, ("push.yml", "checks", "contents"), "Push changes.")
    source = VALID_WORKFLOW.replace(
        "          persist-credentials: false", "          fetch-depth: 0"
    )
    source = source.replace(
        "  checks:\n",
        "  checks:\n    permissions:\n      contents: write # Push repository changes.\n",
    )
    source += "      - run: git push\n"
    assert _findings(source, workflow_name="push.yml", pushing_jobs=frozenset({"checks"})) == []


def test_job_write_all_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ALLOWED_JOB_WRITES, ("ci.yml", "checks", "contents"), "Push changes.")
    source = VALID_WORKFLOW.replace("  checks:\n", "  checks:\n    permissions: write-all\n")
    assert _findings(source, workflow_name="ci.yml") == [
        "checks: job-level permissions must not be write-all"
    ]


@pytest.mark.parametrize("scope", ["contents", "issues", "packages", "id-token", "actions"])
def test_job_write_grant_without_an_exception_is_rejected(scope: str) -> None:
    source = VALID_WORKFLOW.replace(
        "  checks:\n", f"  checks:\n    permissions:\n      {scope}: write\n"
    )
    assert _findings(source) == [f"checks: unauthorized job-level write permission: {scope}"]


@pytest.mark.parametrize(
    "exception",
    [
        ("other.yml", "checks", "contents"),
        ("ci.yml", "other", "contents"),
        ("ci.yml", "checks", "issues"),
    ],
)
def test_job_write_exception_is_scoped(
    monkeypatch: pytest.MonkeyPatch, exception: tuple[str, str, str]
) -> None:
    monkeypatch.setitem(ALLOWED_JOB_WRITES, exception, "Publish an artifact.")
    source = VALID_WORKFLOW.replace(
        "  checks:\n", "  checks:\n    permissions:\n      contents: write\n"
    )
    assert _findings(source, workflow_name="ci.yml") == [
        "checks: unauthorized job-level write permission: contents"
    ]


def test_job_write_exception_needs_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ALLOWED_JOB_WRITES, ("ci.yml", "checks", "contents"), " ")
    source = VALID_WORKFLOW.replace(
        "  checks:\n", "  checks:\n    permissions:\n      contents: write\n"
    )
    assert _findings(source, workflow_name="ci.yml") == [
        "checks: unauthorized job-level write permission: contents"
    ]


@pytest.mark.parametrize("permissions", ["{}", "read-all", "{contents: read, issues: none}"])
def test_read_only_job_permissions_are_allowed(permissions: str) -> None:
    source = VALID_WORKFLOW.replace("  checks:\n", f"  checks:\n    permissions: {permissions}\n")
    assert _findings(source) == []


def test_local_action_needs_no_remote_pin() -> None:
    assert _findings(VALID_WORKFLOW + "      - uses: ./.github/actions/checks\n") == []


def test_quoted_false_disables_checkout_credentials() -> None:
    assert (
        _findings(
            VALID_WORKFLOW.replace("persist-credentials: false", 'persist-credentials: "false"')
        )
        == []
    )

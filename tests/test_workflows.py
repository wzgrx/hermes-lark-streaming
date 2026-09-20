from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"
ACTION_USE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
FULL_SHA = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def test_third_party_actions_are_pinned_to_full_commit_shas() -> None:
    floating = []
    for workflow in WORKFLOWS.glob("*.yml"):
        for action in ACTION_USE.findall(workflow.read_text(encoding="utf-8")):
            if action.startswith("./") or FULL_SHA.fullmatch(action):
                continue
            floating.append(f"{workflow.name}: {action}")
    assert floating == []


def test_release_uses_consolidated_attestation_action() -> None:
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "actions/attest@" in release
    assert "attest-build-provenance" not in release
    assert "subject-path: 'dist/*'" in release


def test_codeql_scans_python_on_pr_push_and_schedule() -> None:
    workflow = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "schedule:" in workflow
    assert "languages: python" in workflow
    assert "security-events: write" in workflow

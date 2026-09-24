from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

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
    assert "softprops/action-gh-release" not in release
    assert "gh release create" in release


def test_codeql_scans_python_on_pr_push_and_schedule() -> None:
    workflow = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "schedule:" in workflow
    assert "languages: python" in workflow
    assert "security-events: write" in workflow


def test_compat_check_reports_only_verification_failures_without_issues(
    tmp_path: Path,
) -> None:
    workflow = yaml.load(
        (WORKFLOWS / "hermes-check.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["check"]["steps"]
    verify = next(step for step in steps if step.get("id") == "verify")
    summary = next(step for step in steps if step.get("name") == "Summarize compatibility failure")
    final = next(step for step in steps if step.get("name") == "Fail job on compatibility break")
    assert verify["continue-on-error"] == "true"
    assert summary["if"] == "always() && steps.verify.outcome == 'failure'"
    assert final["if"] == summary["if"]
    assert final["run"] == "exit 1"
    assert "issues.create" not in summary["run"]

    if shutil.which("bash") is None:
        pytest.skip("Bash is needed to execute the Actions summary script")
    (tmp_path / "hermes-compat-verify.log").write_text(
        "Synthetic missing Gateway anchor\n", encoding="utf-8",
    )
    summary_path = tmp_path / "summary.md"
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary_path),
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "fixture/card",
        "GITHUB_RUN_ID": "123",
    }
    result = subprocess.run(
        ["bash", "-e"], input=summary["run"], text=True,
        capture_output=True, env=env, check=True,
    )
    report = summary_path.read_text(encoding="utf-8")
    assert "Synthetic missing Gateway anchor" in report
    assert "https://github.com/fixture/card/actions/runs/123" in report
    assert "Synthetic missing Gateway anchor" in result.stdout

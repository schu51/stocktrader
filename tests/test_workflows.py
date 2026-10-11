"""Every workflow must be able to tell a human when it fails."""
from pathlib import Path

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
# Started by the owner's own push, so GitHub emails the owner about a failure itself
EMAILED_BY_GITHUB = {"tests.yml"}


def test_every_dispatched_workflow_opens_an_issue_when_it_fails():
    # Runs started by the dispatcher belong to the Actions bot, which emails no
    # one: without this step a failure is silent (weekly review, 2026-10-10).
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert len(files) >= 13
    for f in files:
        if f.name in EMAILED_BY_GITHUB:
            continue
        text = f.read_text()
        assert "if: failure() || cancelled()" in text and "gh issue" in text, f"{f.name} has no failure alert"
        assert "issues: write" in text, f"{f.name} cannot open an issue (no issues: write permission)"


def test_the_suite_runs_on_github_on_the_bots_python():
    text = (WORKFLOWS / "tests.yml").read_text()
    assert "python-version: '3.12'" in text and "pytest" in text
    assert "push:" in text

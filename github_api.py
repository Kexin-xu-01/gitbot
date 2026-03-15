"""
github_api.py — GitHub comment posting and label management.

GITHUB_TOKEN is a phantom token injected by nono. The real token never touches
this process's memory; nono injects it into the Authorization header on calls
to api.github.com.
"""

import logging
from github import Github, GithubException

logger = logging.getLogger(__name__)

LABEL_COLORS: dict[str, str] = {
    "bug": "d73a4a",
    "feature-request": "a2eeef",
    "question": "d876e3",
    "security": "e4e669",
    "needs-info": "fef2c0",
    "duplicate": "cfd3d7",
}


def ensure_label_exists(repo, label_name: str) -> None:
    """Idempotently create a label if it doesn't already exist."""
    try:
        repo.get_label(label_name)
    except GithubException as exc:
        if exc.status == 404:
            color = LABEL_COLORS.get(label_name, "ededed")
            repo.create_label(name=label_name, color=color)
            logger.info("Created label %r on %s", label_name, repo.full_name)
        else:
            raise


def apply_label(issue, label_name: str) -> None:
    """Add a label to an issue."""
    issue.add_to_labels(label_name)
    logger.info("Applied label %r to issue #%d", label_name, issue.number)


def post_comment(issue, body: str) -> None:
    """Post a comment on an issue."""
    issue.create_comment(body)
    logger.info("Posted comment on issue #%d", issue.number)


def post_response(issue_data: dict, triage_result: dict, token: str) -> None:
    """
    Orchestrate the full GitHub response:

    1. Ensure the label exists (create if missing)
    2. Apply the label          ← before comment so it's visible in the notification
    3. Post the triage comment
    4. Log escalation to stdout if needed (human review signal)
    """
    gh = Github(token)
    repo = gh.get_repo(issue_data["repo"])
    issue = repo.get_issue(issue_data["number"])

    label_name = triage_result["label"]

    ensure_label_exists(repo, label_name)
    apply_label(issue, label_name)
    post_comment(issue, triage_result["comment"])

    if triage_result.get("escalate"):
        issue_url = issue.html_url
        reason = triage_result.get("escalation_reason", "(no reason provided)")
        # Stdout escalation — nono policy permits writes to stdout only.
        # A production deployment would forward this to PagerDuty/Slack.
        print(
            f"[ESCALATION REQUIRED] Issue: {issue_url} | Reason: {reason}",
            flush=True,
        )
        logger.warning(
            "Escalation flagged for issue #%d: %s", issue_data["number"], reason
        )

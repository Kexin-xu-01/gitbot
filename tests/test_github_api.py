"""
test_github_api.py — Unit tests for github_api.py

Tests cover:
- Label idempotency (create only when 404)
- Label is applied before comment (ordering)
- Escalation logged to stdout
- No escalation log when escalate=False
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch, call
from github import GithubException

import github_api


def _make_issue_data(number=42, repo="owner/repo", user="alice"):
    return {
        "number": number,
        "title": "Test issue",
        "body": "Some body",
        "user": user,
        "repo": repo,
        "is_first_contribution": False,
    }


def _make_triage_result(label="bug", escalate=False, reason=""):
    return {
        "label": label,
        "comment": "Thanks for the report!",
        "escalate": escalate,
        "escalation_reason": reason,
    }


# ---------------------------------------------------------------------------
# ensure_label_exists
# ---------------------------------------------------------------------------

def test_ensure_label_exists_no_create_when_label_present():
    repo = MagicMock()
    repo.get_label.return_value = MagicMock()  # label exists

    github_api.ensure_label_exists(repo, "bug")

    repo.get_label.assert_called_once_with("bug")
    repo.create_label.assert_not_called()


def test_ensure_label_exists_creates_on_404():
    repo = MagicMock()
    repo.get_label.side_effect = GithubException(404, {"message": "Not Found"}, None)

    github_api.ensure_label_exists(repo, "bug")

    repo.create_label.assert_called_once_with(name="bug", color="d73a4a")


def test_ensure_label_exists_raises_on_non_404():
    repo = MagicMock()
    repo.get_label.side_effect = GithubException(500, {"message": "Server Error"}, None)

    with pytest.raises(GithubException):
        github_api.ensure_label_exists(repo, "bug")


def test_ensure_label_uses_correct_color():
    for label, expected_color in github_api.LABEL_COLORS.items():
        repo = MagicMock()
        repo.get_label.side_effect = GithubException(404, {}, None)
        github_api.ensure_label_exists(repo, label)
        repo.create_label.assert_called_with(name=label, color=expected_color)


# ---------------------------------------------------------------------------
# Label-before-comment ordering
# ---------------------------------------------------------------------------

def test_label_applied_before_comment():
    """apply_label must be called before post_comment in post_response."""
    call_order = []

    issue = MagicMock()
    issue.html_url = "https://github.com/owner/repo/issues/42"
    issue.add_to_labels.side_effect = lambda *a: call_order.append("label")
    issue.create_comment.side_effect = lambda *a: call_order.append("comment")

    repo = MagicMock()
    repo.get_label.return_value = MagicMock()  # label exists
    repo.get_issue.return_value = issue

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo

    with patch("github_api.Github", return_value=gh_mock):
        github_api.post_response(
            _make_issue_data(),
            _make_triage_result(label="bug"),
            token="fake-token",
        )

    assert call_order == ["label", "comment"], (
        f"Expected label then comment, got: {call_order}"
    )


# ---------------------------------------------------------------------------
# Escalation logging
# ---------------------------------------------------------------------------

def test_escalation_printed_to_stdout(capsys):
    issue = MagicMock()
    issue.html_url = "https://github.com/owner/repo/issues/99"
    issue.number = 99
    issue.add_to_labels = MagicMock()
    issue.create_comment = MagicMock()

    repo = MagicMock()
    repo.get_label.return_value = MagicMock()
    repo.get_issue.return_value = issue

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo

    triage_result = _make_triage_result(
        label="security",
        escalate=True,
        reason="Potential sandbox escape via symlink.",
    )

    with patch("github_api.Github", return_value=gh_mock):
        github_api.post_response(_make_issue_data(number=99), triage_result, token="fake")

    captured = capsys.readouterr()
    assert "ESCALATION REQUIRED" in captured.out
    assert "sandbox escape" in captured.out.lower()


def test_no_escalation_log_when_false(capsys):
    issue = MagicMock()
    issue.html_url = "https://github.com/owner/repo/issues/1"
    issue.number = 1
    issue.add_to_labels = MagicMock()
    issue.create_comment = MagicMock()

    repo = MagicMock()
    repo.get_label.return_value = MagicMock()
    repo.get_issue.return_value = issue

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo

    with patch("github_api.Github", return_value=gh_mock):
        github_api.post_response(
            _make_issue_data(number=1),
            _make_triage_result(escalate=False),
            token="fake",
        )

    captured = capsys.readouterr()
    assert "ESCALATION" not in captured.out

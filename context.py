"""
context.py — Context loading for the triage bot.

Fetches nono docs, recent GitHub issues, and the GEMINI.md instruction file.
All expensive network calls are cached; nono only permits GEMINI.md reads after
trust verification passes at startup (enforced by Seatbelt/Sandbox policy).
"""

import time
import logging
import requests
from github import Github, GithubException

logger = logging.getLogger(__name__)

# Module-level caches
_nono_docs_cache: str | None = None
_recent_issues_cache: tuple[list, float] | None = None

_ISSUES_TTL_SECONDS = 300  # 5 minutes


def get_nono_docs() -> str:
    """Fetch nono.sh homepage content. Cached for the process lifetime."""
    global _nono_docs_cache
    if _nono_docs_cache is not None:
        return _nono_docs_cache

    try:
        resp = requests.get("https://nono.sh", timeout=10)
        resp.raise_for_status()
        _nono_docs_cache = resp.text
        logger.info("Fetched nono docs (%d chars)", len(_nono_docs_cache))
    except Exception as exc:
        logger.warning("Could not fetch nono docs: %s", exc)
        _nono_docs_cache = ""

    return _nono_docs_cache


def get_recent_issues(repo_name: str, token: str) -> list[dict]:
    """
    Return the last 20 closed+open issues as dicts. TTL-cached (5 min).

    Each dict: {"number": int, "title": str, "state": str, "user": str}
    """
    global _recent_issues_cache

    now = time.monotonic()
    if _recent_issues_cache is not None:
        cached_list, ts = _recent_issues_cache
        if now - ts < _ISSUES_TTL_SECONDS:
            return cached_list

    issues = []
    try:
        gh = Github(token)
        repo = gh.get_repo(repo_name)
        for issue in repo.get_issues(state="all", sort="created", direction="desc"):
            if issue.pull_request:
                continue
            issues.append(
                {
                    "number": issue.number,
                    "title": issue.title,
                    "state": issue.state,
                    "user": issue.user.login if issue.user else "unknown",
                }
            )
            if len(issues) >= 20:
                break
        logger.info("Fetched %d recent issues for %s", len(issues), repo_name)
    except GithubException as exc:
        logger.warning("Could not fetch recent issues: %s", exc)

    _recent_issues_cache = (issues, now)
    return issues


def load_gemini_md() -> str:
    """
    Read GEMINI.md from disk.

    Under nono, the Seatbelt/Sandbox policy only permits this open() call AFTER
    'nono trust verify GEMINI.md' succeeds at startup. If GEMINI.md has been
    tampered with, the process will have already exited before reaching this call.
    """
    with open("GEMINI.md", "r", encoding="utf-8") as fh:
        return fh.read()


def build_context(issue_data: dict, token: str) -> dict:
    """
    Assemble all context needed for triage and enrich issue_data in-place.

    Returns a dict with keys:
      - nono_docs: str (truncated)
      - recent_issues: list[dict]
      - gemini_md: str
      - is_first_contribution: bool (also written into issue_data)
    """
    repo_name = issue_data["repo"]
    reporter = issue_data["user"]

    nono_docs = get_nono_docs()
    recent_issues = get_recent_issues(repo_name, token)
    gemini_md = load_gemini_md()

    # Determine if this is the reporter's first issue in this repo
    reporter_previous = [i for i in recent_issues if i["user"] == reporter]
    is_first = len(reporter_previous) == 0
    issue_data["is_first_contribution"] = is_first

    return {
        "nono_docs": nono_docs[:3000],  # keep prompt within budget
        "recent_issues": recent_issues,
        "gemini_md": gemini_md,
        "is_first_contribution": is_first,
    }


def warm_cache(repo_name: str, token: str) -> None:
    """Pre-populate caches at startup so the first webhook responds quickly."""
    logger.info("Warming context cache...")
    get_nono_docs()
    get_recent_issues(repo_name, token)
    logger.info("Context cache warmed.")

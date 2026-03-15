"""
bot.py — Flask webhook server, startup trust verification, pipeline orchestration.

Startup sequence:
  1. nono trust verify GEMINI.md  (subprocess — exits with error if tampered)
  2. Warm context cache (nono docs + recent issues)
  3. Start Flask on port 5000

Route POST /webhook:
  validate_signature → parse_event → build_context → run_triage → post_response → 200 OK

Route GET /debug/show-token  (dev only — demonstrates phantom token behavior)
Route GET /debug/read-ssh    (dev only — demonstrates filesystem block behavior)
"""

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys

from dotenv import load_dotenv
from flask import Flask, request, jsonify

import context as ctx
import triage
import github_api

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration (env vars; real values injected by nono credential store in prod)
# ---------------------------------------------------------------------------

GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
GITHUB_REPO: str = os.environ.get("GITHUB_REPO", "")  # e.g. "owner/repo"


# ---------------------------------------------------------------------------
# Startup: trust verification
# ---------------------------------------------------------------------------

def verify_gemini_md_trust() -> None:
    """
    Call 'nono trust verify GEMINI.md'.

    If nono is not installed (dev mode without nono), emit a warning and continue.
    If nono IS installed and verification fails, exit(1).
    """
    try:
        result = subprocess.run(
            ["nono", "trust", "verify", "GEMINI.md"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.critical(
                "FATAL: GEMINI.md trust verification failed.\n%s\n"
                "Re-sign with: nono trust sign GEMINI.md",
                result.stderr,
            )
            sys.exit(1)
        logger.info("GEMINI.md trust verification passed.")
    except FileNotFoundError:
        # nono binary not found — dev mode, proceed with warning
        logger.warning(
            "nono binary not found — skipping trust verification (dev mode). "
            "Run under 'nono run' in production."
        )


# ---------------------------------------------------------------------------
# Webhook validation
# ---------------------------------------------------------------------------

def validate_signature(req) -> bool:
    """
    Validate GitHub's HMAC-SHA256 webhook signature.

    Returns True if valid, False otherwise.
    Header: X-Hub-Signature-256: sha256=<hex>
    """
    sig_header = req.headers.get("X-Hub-Signature-256", "")
    if not sig_header.startswith("sha256="):
        return False

    expected_sig = sig_header[len("sha256="):]
    body = req.get_data()
    computed = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, expected_sig)


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def parse_event(payload: dict) -> dict | None:
    """
    Extract structured issue data from a GitHub webhook payload.

    Returns None if the event should be ignored (not issues.opened).
    """
    action = payload.get("action")
    if action != "opened":
        return None

    issue = payload.get("issue")
    if not issue:
        return None

    repo_data = payload.get("repository", {})
    repo_full_name = repo_data.get("full_name", GITHUB_REPO)

    return {
        "number": issue["number"],
        "title": issue.get("title", ""),
        "body": issue.get("body", "") or "",
        "user": issue.get("user", {}).get("login", "unknown"),
        "repo": repo_full_name,
        "is_first_contribution": False,  # enriched by build_context
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    # 1. Validate signature
    if not validate_signature(request):
        logger.warning("Rejected webhook: invalid signature")
        return jsonify({"error": "invalid signature"}), 401

    # 2. Parse event
    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "bad JSON"}), 400

    issue_data = parse_event(payload)
    if issue_data is None:
        # Not an issues.opened event — silently accept (prevents GitHub retries)
        return jsonify({"status": "ignored"}), 200

    logger.info(
        "Triaging issue #%d: %r on %s",
        issue_data["number"],
        issue_data["title"],
        issue_data["repo"],
    )

    # 3. Build context (enriches issue_data with is_first_contribution)
    try:
        context = ctx.build_context(issue_data, GITHUB_TOKEN)
    except Exception as exc:
        logger.error("Context build failed: %s", exc)
        return jsonify({"status": "error", "detail": "context"}), 200  # 200 to avoid retry

    # 4. Run triage
    try:
        triage_result = triage.run_triage(context, issue_data, GEMINI_API_KEY)
    except Exception as exc:
        logger.error("Triage failed: %s", exc)
        return jsonify({"status": "error", "detail": "triage"}), 200

    logger.info(
        "Triage result for #%d: label=%r escalate=%s",
        issue_data["number"],
        triage_result["label"],
        triage_result["escalate"],
    )

    # 5. Post response to GitHub
    try:
        github_api.post_response(issue_data, triage_result, GITHUB_TOKEN)
    except Exception as exc:
        logger.error("GitHub post failed: %s", exc)
        return jsonify({"status": "error", "detail": "github"}), 200

    return jsonify({"status": "ok", "label": triage_result["label"]}), 200


@app.route("/debug/show-token", methods=["GET"])
def debug_show_token():
    """
    Dev endpoint — demonstrates phantom token behavior.

    Under nono, GITHUB_TOKEN is a phantom string (not the real ghp_... token),
    yet API calls still succeed because nono injects the real credential at the
    network layer. This endpoint returns what the process actually sees.
    """
    token = os.environ.get("GITHUB_TOKEN", "(not set)")
    # Show only first/last 4 chars to avoid logging real tokens in dev
    if len(token) > 8:
        display = token[:4] + "..." + token[-4:]
    else:
        display = token
    return jsonify({"token_seen_by_process": display})


@app.route("/debug/read-ssh", methods=["GET"])
def debug_read_ssh():
    """
    Dev endpoint — demonstrates filesystem block behavior.

    Under nono, policy.nono.toml denies reads from ~/.ssh/**. This endpoint
    attempts to open ~/.ssh/id_rsa and should receive EPERM.
    """
    ssh_key_path = os.path.expanduser("~/.ssh/id_rsa")
    try:
        with open(ssh_key_path, "r") as fh:
            content = fh.read(64)
        return jsonify({"status": "read_succeeded (nono NOT enforcing)", "preview": content[:20]})
    except PermissionError:
        return jsonify({"status": "blocked_by_nono (EPERM — expected)"}), 403
    except FileNotFoundError:
        return jsonify({"status": "file_not_found (no SSH key at default path)"}), 404


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Validate required config
    missing = [v for v in ["GITHUB_TOKEN", "GEMINI_API_KEY", "WEBHOOK_SECRET", "GITHUB_REPO"]
               if not os.environ.get(v)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    # Step 1: Trust verification
    verify_gemini_md_trust()

    # Step 2: Warm cache
    ctx.warm_cache(GITHUB_REPO, GITHUB_TOKEN)

    # Step 3: Start server
    logger.info("Starting gitbot on port 5000...")
    app.run(host="0.0.0.0", port=5000, debug=False)

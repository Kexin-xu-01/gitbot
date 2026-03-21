# gitbot — nono GitHub Issue Triage Bot

An automated GitHub issue triage bot that labels incoming issues and posts an AI-generated first response using nono docs and recent issues as context. Security-sensitive reports are flagged for human review.

Gitbot is also a **nono use case**: it runs as a persistent process with credentials, makes outbound network calls, and loads an instruction file (`GEMINI.md`) whose integrity is verified by `nono trust` before the process can read it.

```
GitHub Issue Opened
       │
       ▼
  smee.io / ngrok
  (webhook relay)
       │
       ▼
  POST /webhook
  ┌────────────────────────────────────────┐
  │  validate HMAC-SHA256 signature        │
  │  parse event (issues.opened only)      │
  │  build_context()                       │
  │    ├─ nono docs  (nono.sh, cached)     │
  │    ├─ recent issues  (GitHub, TTL 5m)  │
  │    └─ GEMINI.md  (trust-verified)      │
  │  run_triage()  ──► Gemini 2.5 Flash   │
  │  post_response()                       │
  │    ├─ ensure label exists              │
  │    ├─ apply label                      │
  │    ├─ post comment                     │
  │    └─ log escalation (if security)     │
  └────────────────────────────────────────┘
       │
       ▼
  200 OK  (always, to prevent GitHub retries)
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- `nono` CLI
- A GitHub repo with admin access
- A GitHub personal access token with `issues:write` and `repo` scopes
- A Gemini API key

### 2. Install

```bash
git clone https://github.com/Kexin-xu-01/gitbot.git
cd gitbot
pip3 install -r requirements.txt
```

### 3. Configure GitHub webhook

1. Go to your GitHub repo → Settings → Webhooks → Add webhook
2. **Payload URL**: your smee.io channel URL (create one at [smee.io](https://smee.io))
3. **Content type**: `application/json`
4. **Secret**: a random string — you'll store this as `WEBHOOK_SECRET` in the next step
5. **Events**: select "Issues" only

### 4. Store credentials in Apple Passwords

GitHub token and Gemini API key are injected by nono from Apple Passwords before the sandbox is applied. The webhook secret is passed as a plain env var (nono's thread limit prevents loading all 3 via keychain).

```bash
# GitHub personal access token
security add-internet-password -s "github.com" -a "your-github-username" -w "ghp_your_real_token"

# Gemini API key
security add-internet-password -s "generativelanguage.googleapis.com" -a "your-account" -w "your_gemini_key"
```

### 5. Generate your signing key and embed it

The repo ships with the original author's public key in `trust-policy.json`. Replace it with your own so nono trusts your signatures on `GEMINI.md`.

```bash
# Generate a key (stored in nono's keystore, no file to commit)
nono trust keygen --id gitbot

# Copy the output of this command
nono trust export-key --id gitbot
```

Paste the output into `trust-policy.json` as `public_key` and set `key_id` to `gitbot`:

```json
{
  "version": 1,
  "publishers": [
    {
      "name": "local-dev",
      "key_id": "gitbot",
      "public_key": "<paste nono trust export-key output here>"
    }
  ],
  "includes": ["GEMINI.md"],
  "blocklist": { "digests": [], "publishers": [] },
  "enforcement": "deny"
}
```

### 6. Sign the files and commit

```bash
nono trust sign --key gitbot GEMINI.md   # creates GEMINI.md.bundle
nono trust sign-policy --key default     # signs with default key so nono can bootstrap the policy

nono trust verify GEMINI.md --policy ./trust-policy.json   # should exit 0

git add trust-policy.json trust-policy.json.bundle GEMINI.md.bundle
git commit -m "Embed my signing key and sign instruction files"
```

### 7. Run

In one terminal, start the bot:

```bash
WEBHOOK_SECRET=your_webhook_secret \
GITHUB_REPO=owner/repo \
nono run --profile gitbot-profile.json --allow-cwd --listen-port 5001 \
  --env-credential-map 'apple-password://github.com/your-github-username' GITHUB_TOKEN \
  --env-credential-map 'apple-password://generativelanguage.googleapis.com/your-account' GEMINI_API_KEY \
  -- python3 bot.py
```

In a second terminal, forward webhooks:

```bash
npm install --global smee-client
smee --url https://smee.io/your-channel --port 5001 --path /webhook
```

Add `DEBUG=1` before `GITHUB_REPO` to enable verbose logging and Flask debug mode.

Use smee's event replay button to re-test without opening new GitHub issues.

---

## Modifying bot behavior

Edit `GEMINI.md`, then re-sign and commit:

```bash
nono trust sign --key gitbot GEMINI.md
git add GEMINI.md GEMINI.md.bundle
git commit -m "Update and re-sign bot instructions"
```

---

## nono Security Features

- **Trust verification** — `GEMINI.md` is verified before the process can read it; tampering causes an immediate exit
- **Filesystem policy** — reads from `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gcloud`, `~/.kube` are blocked at the kernel level
- **Network policy** — only `api.github.com`, `generativelanguage.googleapis.com`, `nono.sh`, and `smee.io` are reachable outbound
- **Credential injection** — credentials are pulled from Apple Passwords by nono before the sandbox applies; the process never sees the raw keychain

---

## Tests

```bash
pytest tests/ -v
```

- `test_triage.py` — JSON parsing, label validation, prompt length budget
- `test_github_api.py` — label idempotency, label-before-comment ordering, escalation logging
- `test_bot.py` — HMAC validation, event filtering, pipeline smoke test

---

## Production Path

- Sign `GEMINI.md` automatically in CI using [nono-attest](https://github.com/marketplace/actions/nono-attest) and switch the trust policy to a keyless OIDC publisher
- Replace smee.io with a real public HTTPS endpoint
- Run under `nono wrap` as PID 1 in a container
- Store credentials in the cloud keychain, not Apple Passwords

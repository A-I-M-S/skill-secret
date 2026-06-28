---
name: skill-secret
description: Manages an encrypted note vault backed by a Notion database. Stores password-protected notes and returns the single best-matching note in response to a natural-language query. Search runs server-side on Notion.
---

# Secret Courier Vault (v3)

Use this skill to store secrets as notes in a private Notion database and to
retrieve them later via a natural-language query. The Notion API token is
itself encrypted under the user's password and stored locally in `.env`; the
script invokes the Notion API to do the actual search and returns only the
single best-matching note. You must always invoke the `secret.py` script via
your execution tool. The project ships a `bin/secret` wrapper that dispatches
to the project venv (`.venv/bin/python`) — use it instead of `python3
secret.py` so the right environment is on PYTHONPATH.

The first run creates a Notion database (`skill-secret-vault`) under the
parent page the user supplies, then writes `.env` next to the working
directory. Subsequent runs read `.env`, decrypt the Notion token, and call
the Notion API.

## 🛡️ CORE RULES

1. **Never reveal passwords** in your responses. The user provides the
   password; you pass it via `--password` and never echo it.
2. **Never reveal the Notion API token** in your responses. It lives in
   `.env` (encrypted at rest with the user's password) and you must not
   print it, log it, or surface it on the wire.
3. **Never output the entire database** of notes. Only the single
   best-matching note from `retrieve` is returned. There is intentionally
   no `--list` or `--dump` flag.

## 🛠️ Commands

Every command accepts the optional top-level `--env-file PATH` flag, which
overrides `$SKILL_SECRET_ENV` and the default `./$CWD/.env` lookup.

### 1. `init` — initialize a new KMS database

```
bin/secret init \
    --env-file "$ENV" \
    --notion-token "<notion_internal_integration_token>" \
    --parent-page-id "<notion_page_uuid>" \
    --password "<password>"
```

- Creates a Notion database titled `skill-secret-vault` under the supplied
  parent page (the parent page must already be shared with the integration).
- Encrypts the Notion token under the user's password (PBKDF2-HMAC-SHA256,
  720,000 iterations, 16-byte salt) and writes the base64 blob plus the
  database id and parent page id to `.env` with file mode `0o600`.
- The plaintext Notion token is **never** written to disk; only its
  ciphertext is.

Refuses to run if `.env` already exists at the resolved path (exit code 5).

### 2. `take` — store a note in the KMS

```
bin/secret take \
    --env-file "$ENV" \
    --password "<password>" \
    --content "<note body>"
```

- Reads `.env`, decrypts the Notion token using the password, and creates a
  new page in the `skill-secret-vault` database whose body is `--content`.
- `--content` may be any length; Notion enforces its own page-size limit.

### 3. `retrieve` — fetch the top-1 matching note

```
bin/secret retrieve \
    --env-file "$ENV" \
    --password "<password>" \
    --query "<natural_language_query>"
```

- Reads `.env`, decrypts the Notion token, and runs a Notion-side search
  over the `skill-secret-vault` database for pages matching `--query`.
- Notion returns the top result; the script prints only that one page's
  body to stdout. Other pages in the database are never decrypted into the
  agent's context.
- Writes `MODE: notion` to stderr before printing the result.

### 4. `whoami` — show sanitized account info

```
bin/secret whoami \
    --env-file "$ENV" \
    --password "<password>"
```

- Reads `.env`, decrypts the Notion token, calls Notion's `/v1/users/me`
  endpoint, and prints `bot_id` and `workspace_name` (sanitized: empty
  string if Notion returns a falsy value). Does **not** print the Notion
  token, the page id, the database id, or any other credential material.

### Top-level `--env-file` flag

```
bin/secret [--env-file PATH] <subcommand> [...]
```

- If `--env-file PATH` is given, that path is used.
- Else `$SKILL_SECRET_ENV` (if set) is used.
- Else `./.env` relative to the current working directory is used.

This lets users keep multiple vaults side by side (e.g. `--env-file
~/.env.work`, `--env-file ~/.env.personal`) without the agent having to
manage `cd` between runs.

## 🚨 Error paths

All errors are printed to stdout as a single `ERROR:` line. Match the string
verbatim to map to a user-facing message.

| Condition | stdout | Exit |
|---|---|---|
| Not initialized (`.env` missing) | `ERROR: Not initialized. Run init first.` | 4 |
| Already initialized (`.env` exists at init time) | `ERROR: Already initialized. Delete .env to re-init.` | 5 |
| `.env` unreadable (permissions, malformed, missing keys) | `ERROR: .env is unreadable: <reason>` | 4 |
| Wrong password (decrypting the Notion token fails) | `ERROR: Wrong password.` | 2 |
| Notion API error during init / take / retrieve / whoami | `ERROR: Notion API error: <reason>` | 3 |
| Notion rejected the integration token at init time | `ERROR: Notion API rejected the token: <reason>` | 3 |
| Notion could not create the database under the parent page | `ERROR: Could not create database: <reason>` | 3 |
| Notion could not write the bootstrap page (db_id + blob) | `ERROR: Could not write bootstrap: <reason>` | 3 |
| Could not write `.env` to disk | `ERROR: Could not write .env: <reason>` | 3 |
| Bad CLI arguments | argparse usage to stderr | 2 |

## 🔒 Security properties (v3)

- **PBKDF2-HMAC-SHA256, 720,000 iterations** for key derivation. This
  matches the OWASP 2024 recommendation and is brute-force resistant
  against casual attackers.
- **Per-init 16-byte random salt.** The Notion token is encrypted with a
  fresh salt each `init`; the salt is embedded in the ciphertext blob so
  decryption does not need to consult `.env` for it.
- **Fernet token (AES-128-CBC + HMAC-SHA256).** The encrypted Notion
  token is a Fernet payload; any tamper to the blob, salt, or iteration
  count is detected at decrypt time.
- **The Notion API key is the only secret.** The user's password is a
  passphrase, not a secret — it is not stored on disk in any form. The
  Notion token is stored only as the PBKDF2+Fernet ciphertext blob inside
  `.env`, which is written with file mode `0o600` (and its parent
  directory with `0o700`).
- **Search happens server-side.** The script never decrypts note bodies
  into the local process. `retrieve` asks Notion for the top match and
  prints only that single page's body. Other notes are never read, never
  cached, never returned.
- **No password recovery.** Forgetting the password means losing the
  ability to decrypt the Notion token — i.e. losing the ability to talk
  to the KMS. The notes themselves are still on Notion, but they are
  unreachable from this skill without the password. There is no backdoor
  and there cannot be one in this design.

## 🧪 Exit-code summary

- `0` — operation succeeded (including "no confident match" and "empty
  database"; these are not errors, they are normal outcomes)
- `1` — unexpected internal error
- `2` — wrong password, or bad CLI arguments
- `3` — Notion API error (rejected token, failed create, etc.)
- `4` — not initialized, or `.env` missing / unreadable
- `5` — already initialized (refusing to overwrite `.env`)

Exit code 0 does **not** mean a match was found. Always read stdout and
pattern-match on the specific success/failure strings listed above.

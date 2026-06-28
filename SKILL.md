---
name: skill-secret
description: Manages an encrypted note vault backed by a Supabase Postgres table. Stores password-protected notes and returns the single best-matching note in response to a natural-language query. Search runs server-side via a Postgres full-text search function defined in setup.sql.
---

# Secret Courier Vault (v4)

Use this skill to store secrets as rows in a private Supabase Postgres
`notes` table and to retrieve them later via a natural-language query.
The Supabase anon key is itself encrypted under the user's password and
stored locally in `.env`; the script invokes the Supabase API to do the
actual search and returns only the single best-matching note. You must
always invoke the `secret.py` script via your execution tool. The project
ships a `bin/secret` wrapper that dispatches to the project venv
(`.venv/bin/python`) — use it instead of `python3 secret.py` so the right
environment is on PYTHONPATH.

The first run creates the schema in your Supabase project by calling
`kms.whoami()` and `kms.ensure_schema()` against it. **You must run the
SQL in `setup.sql` once in the Supabase SQL editor before `init`
succeeds** — `ensure_schema()` verifies the `notes` table exists but
does not auto-migrate. Subsequent runs read `.env`, decrypt the anon
key, and call the Supabase API.

## 🛡️ CORE RULES

1. **Never reveal passwords** in your responses. The user provides the
   password; you pass it via `--password` and never echo it.
2. **Never reveal the Supabase anon key** in your responses. It lives
   in `.env` (encrypted at rest with the user's password) and you must
   not print it, log it, or surface it on the wire.
3. **Never output the entire database** of notes. Only the single
   best-matching note from `retrieve` is returned. There is intentionally
   no `--list` or `--dump` flag.

## 🚀 First-run walkthrough

Before the first `init`, the user needs a Supabase project with the
`notes` table provisioned:

1. Sign up at <https://supabase.com> and create a new project.
2. From the project's **Settings → API** page, copy the **Project URL**
   (looks like `https://abcdefghij.supabase.co`) and the **`anon`
   public** key (a long JWT starting with `eyJ...`).
3. In the Supabase dashboard, open **SQL Editor**, paste the contents
   of `setup.sql` (shipped at the repo root), and run it once. This
   creates the `notes` table, the `search_notes(...)` function, and the
   supporting index.
4. Run `bin/secret init --url … --api-key … --password …` (see below).

The user must complete steps 1–3 before `init` is invoked; otherwise
`ensure_schema()` fails with `ERROR: Could not verify schema: …` (exit
code 3) and the `.env` is not written.

## 🛠️ Commands

Every command accepts the optional top-level `--env-file PATH` flag, which
overrides `$SKILL_SECRET_ENV` and the default `./$CWD/.env` lookup.

### 1. `init` — initialize a new KMS database

```
bin/secret init \
    --env-file "$ENV" \
    --url "https://abcdefghij.supabase.co" \
    --api-key "***" \
    --password "<password>"
```

- Calls `kms.whoami()` and `kms.ensure_schema()` against your Supabase
  project. You must run `setup.sql` once in the Supabase SQL editor
  before `init` succeeds.
- Encrypts the Supabase anon key under the user's password (PBKDF2-HMAC-
  SHA256, 720,000 iterations, 16-byte salt) and writes the base64 blob
  plus the project URL to `.env` with file mode `0o600`.
- The plaintext anon key is **never** written to disk; only its
  ciphertext is.

Refuses to run if `.env` already exists at the resolved path (exit code 5).

### 2. `take` — store a note in the KMS

```
bin/secret take \
    --env-file "$ENV" \
    --password "<password>" \
    --content "<note body>"
```

- Reads `.env`, decrypts the anon key using the password, and inserts a
  row into the `notes` Postgres table via `kms.create_note()` whose body
  is `--content`.
- `--content` may be any length; the row body is plain `text` in
  Postgres.

### 3. `retrieve` — fetch the top-1 matching note

```
bin/secret retrieve \
    --env-file "$ENV" \
    --password "<password>" \
    --query "<natural_language_query>"
```

- Reads `.env`, decrypts the anon key, and calls the
  `search_notes(...)` Postgres function via Supabase RPC and prints the
  top-1 row's body.
- Postgres returns the top result; the script prints only that one
  row's body to stdout. Other rows in `notes` are never decrypted into
  the local process; the function returns only the top match.
- Writes `MODE: supabase` to stderr before printing the result.

### 4. `whoami` — show sanitized account info

```
bin/secret whoami \
    --env-file "$ENV" \
    --password "<password>"
```

- Reads `.env`, decrypts the anon key, calls `auth.get_user(jwt=...)`
  against Supabase and prints sanitized info: `project_url`,
  `anon_key_id` (first 8 chars + `…`), `region`, `auth_status`. Does
  **not** print the anon key, the project URL, or any other credential
  material beyond the redacted `anon_key_id`.

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
| `.env` is from v3 (`SKILL_SECRET_KMS_DB_ID` / `SKILL_SECRET_KMS_PARENT_PAGE_ID` present without v4 backend) | `ERROR: .env is unreadable: <reason>` | 4 |
| Unknown backend (`.env` is not `SKILL_SECRET_KMS_BACKEND=supabase`) | `ERROR: Unsupported backend: <reason>` | 4 |
| Wrong password (decrypting the anon key fails) | `ERROR: Wrong password.` | 2 |
| Supabase API error during init / take / retrieve / whoami | `ERROR: Supabase API error: <reason>` | 3 |
| Supabase rejected the api-key at init time | `ERROR: Supabase API rejected the api-key: <reason>` | 3 |
| Could not verify schema (the `notes` table is missing — `setup.sql` was not run) | `ERROR: Could not verify schema: <reason>` | 3 |
| Could not write bootstrap row (`kind='bootstrap'`) to `notes` | `ERROR: Could not write bootstrap: <reason>` | 3 |
| Could not write `.env` to disk | `ERROR: Could not write .env: <reason>` | 3 |
| Bad CLI arguments | argparse usage to stderr | 2 |

## 🔒 Security properties (v4)

- **PBKDF2-HMAC-SHA256, 720,000 iterations** for key derivation. This
  matches the OWASP 2024 recommendation and is brute-force resistant
  against casual attackers.
- **Per-init 16-byte random salt.** The Supabase anon key is encrypted
  with a fresh salt each `init`; the salt is embedded in the ciphertext
  blob so decryption does not need to consult `.env` for it.
- **Fernet token (AES-128-CBC + HMAC-SHA256).** The encrypted anon key
  is a Fernet payload; any tamper to the blob, salt, or iteration
  count is detected at decrypt time.
- **The Supabase anon key is the only secret.** The user's password is a
  passphrase, not a secret — it is not stored on disk in any form. The
  anon key is stored only as the PBKDF2+Fernet ciphertext blob inside
  `.env`, which is written with file mode `0o600` (and its parent
  directory with `0o700`).
- **Search happens server-side.** The script never decrypts note bodies
  into the local process. `retrieve` asks Postgres for the top match
  and prints only that single row's body. Other notes are never read,
  never cached, never returned. The bootstrap row lives in the
  `notes` Postgres table with `kind='bootstrap'`; the anon key
  ciphertext can be recovered with the password.
- **No password recovery.** Forgetting the password means losing the
  ability to decrypt the anon key — i.e. losing the ability to talk to
  the KMS. The notes themselves are still in Supabase, but they are
  unreachable from this skill without the password. There is no
  backdoor and there cannot be one in this design.

## 🧪 Exit-code summary

- `0` — operation succeeded (including "no confident match" and "empty
  database"; these are not errors, they are normal outcomes)
- `1` — unexpected internal error
- `2` — wrong password, or bad CLI arguments
- `3` — Supabase API error (rejected api-key, failed insert, etc.)
- `4` — not initialized, or `.env` missing / unreadable
- `5` — already initialized (refusing to overwrite `.env`)

Exit code 0 does **not** mean a match was found. Always read stdout and
pattern-match on the specific success/failure strings listed above.
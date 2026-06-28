# Changelog

All notable changes to skill-secret are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-06-28

### Changed (BREAKING)

- **Architecture: local file vault → Notion-backed KMS.** The v2 `vault.enc` file and
  the `encrypt` / `decrypt` subcommands are gone. The vault now lives in a Notion
  database that you control.
- **Subcommands replaced.** `init` / `take` / `retrieve` / `whoami` replace
  `encrypt` / `decrypt`.
- **New `init` flow.** Creates (or reuses) a Notion database named
  `skill-secret-vault` under a parent page that you share with your Notion
  integration, encrypts the Notion API key with your password, and writes the
  encrypted blob plus the database id and parent page id to a `0o600` `.env` file.
- **`.env` is the local state.** Holds the encrypted Notion API key, the database
  id, and the parent page id. The plaintext token never appears on disk.
- **Search happens server-side.** Notion's API does the ranking. No local index,
  no model download, no vector store on the agent host.

### Added

- `init` — one-time setup: create the Notion database, write the bootstrap record,
  write `.env`.
- `take` — store a note (one Notion page per call). The first 80 chars of the
  content become the page title (or a UUID if empty).
- `retrieve` — fetch the top-1 matching note, never more. The rest of the vault
  stays out of context. Writes `MODE: notion` to stderr on every successful
  invocation (top-1 match OR no-match).
- `whoami` — sanity-check the token by calling `users.me()`; prints sanitized
  account info (bot id + workspace name). Never echoes the token itself.
- `bin/secret` wrapper — dispatches to the venv's Python, keeps `cryptography` +
  `notion-client` on `PYTHONPATH`.
- `--env-file PATH` — top-level flag overriding `$SKILL_SECRET_ENV` and `./.env`
  (resolution order: `--env-file` → `$SKILL_SECRET_ENV` → `./.env` in cwd).
- `crypto.py` — public surface: `encrypt_token`, `decrypt_token`, `derive_key`,
  `DEFAULT_PBKDF2_ITERS`, `SALT_SIZE`, `InvalidToken`.
- `notion_kms.py` — `NotionKMS` adapter: `whoami`, `ensure_database`,
  `create_page`, `search`, `get_bootstrap`, `set_bootstrap`. `notion_client` is
  imported lazily so the module is importable without the package installed.
- `envfile.py` — atomic `.env` read / write, mode `0o600`, sorted-key
  serialization.
- `__bootstrap__` page — a single Notion page in the vault database holding the
  encrypted Notion API key. Provides a recovery path: if the local `.env` is lost,
  the key can be recovered from the KMS using the password.

### Removed

- Local file vault (`vault.enc`) and all v2 file-vault code (`read_vault` /
  `write_vault` / chunked encryption / header MAC machinery).
- `encrypt` / `decrypt` subcommands and all v2 CLI plumbing.
- Semantic-search stack: `sentence-transformers`, `torch`, `numpy`,
  `transformers`, `huggingface-hub`, `tokenizers`, `safetensors`. Notion does
  the search now.
- `STOPWORDS` / `_tokenize` / `score_keyword` / `score_semantic` /
  `_have_semantic` helpers.
- `test_secret.py` (17 tests of removed v2 file-vault code).
- "Migrating from v0.01" README section (two major versions back, irrelevant
  now).
- "Dependency modes" SKILL.md section (no semantic/keyword mode in v3).

### Security

- **PBKDF2-HMAC-SHA256, 720,000 iterations** for key derivation of the API key
  blob (OWASP 2024). Per-blob 16-byte random salt.
- **Fernet (AES-128-CBC + HMAC-SHA256)** wraps the Notion API key. Self-
  contained blob: `[16B salt | 4B iters LE | Fernet token]`.
- **The Notion API key is the only secret.** It is encrypted at rest in `.env`
  and in the `__bootstrap__` page in the Notion database.
- **`.env` is `0o600`**, written atomically via `os.open(..., 0o600)` + `fsync`
  + `os.replace`. `.env` is in `.gitignore`.
- **The agent never sees the plaintext token in conversation context.** Only
  the result of Notion API calls is observable.
- **No password recovery.** Forgetting the password means re-running `init`
  against a fresh Notion database; old pages remain in Notion but are
  unreachable without the password.
- **Network: only outbound to `api.notion.com` over HTTPS** via `notion-client`.
  No other endpoints.

### Exit codes

- `0` — operation succeeded (including "no confident match"; this is a normal
  outcome for `retrieve`).
- `2` — wrong password, or bad CLI arguments.
- `3` — Notion API error (or init-time setup error).
- `4` — not initialized (`.env` missing or unreadable).
- `5` — already initialized (re-init blocked).

### Migration from v2

The v2 file vault and the v2 CLI are gone; there is no in-place migration tool
by design. To move forward:

1. While you still have access to your v2 vault, note down its contents via the
   v2 `secret.py decrypt` flow.
2. Create a fresh Notion integration + parent page for v3 (see README "Setup"
   and "First run").
3. Run `bin/secret init …` to create a fresh v3 database.
4. For each v2 chunk, run `bin/secret take --password *** --content <chunk>`.
5. Delete the old `vault.enc` after confirming v3 works.

See the README "v2 → v3 migration" section for the full walkthrough.

## [2.0.0] - 2024

PBKDF2 key derivation, per-chunk encryption, keyword fallback. See commit
history for the full diff.

## [1.0.0] - 2024

Initial release: single Fernet blob, no PBKDF2, local file vault.

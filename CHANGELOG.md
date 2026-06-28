# Changelog

All notable changes to skill-secret are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.0.0] - 2026-06-28

### Changed (BREAKING)

- **Architecture: Notion-backed KMS → Supabase-backed KMS.** The v3
  `notion-client` adapter is gone. The vault now lives as rows in a `notes`
  Postgres table in a Supabase project that you control.
- **`.env` schema replaced.** v3's `SKILL_SECRET_KMS_DB_ID` and
  `SKILL_SECRET_KMS_PARENT_PAGE_ID` (plus encrypted `SKILL_SECRET_KMS_API_BLOB`
  holding a Notion token) are replaced by `SKILL_SECRET_KMS_BACKEND=supabase`,
  `SKILL_SECRET_KMS_PROJECT_URL` (the `https://<ref>.supabase.co` URL), and
  `SKILL_SECRET_KMS_API_BLOB` (now holding the encrypted Supabase **anon key**).
  The v3 keys are not read; v4 raises a migration error if it sees them.
- **`init` flags replaced.** `--database-id` / `--parent-page-id` are gone. v4's
  `init` takes `--url <supabase_url> --api-key ***` (the project URL + the
  `anon` / `public` key from the Supabase dashboard) and your `--password ***`
  to encrypt the key.
- **Schema bootstrapping is manual.** v4 ships a `setup.sql` file that you
  paste into the Supabase SQL editor once. It creates the `notes` table, the
  GIN index on `tsvector(body)`, and the `search_notes(query_text, max_results)`
  PL/pgSQL function (uses `websearch_to_tsquery` + `ts_rank`). `init` verifies
  the schema with a `select 1 from notes limit 1` and raises a pointer to
  `setup.sql` if the table is missing.
- **Search is server-side Postgres FTS.** v3 leaned on Notion's search ranking.
  v4 calls a single `rpc("search_notes", {"query_text": ..., "max_results": 1})`
  per `retrieve`. The agent host never holds an index, a model, or a vector
  store.
- **Whoami format changed.** v3 printed `bot_id / workspace` (from Notion
  `users.me`). v4 prints `project / anon_key` (project ref derived from the
  URL, plus a fingerprint of the anon key — never the key itself).

### Added

- `setup.sql` — idempotent schema for the `notes` table + `search_notes()`
  function. Paste once into the Supabase SQL editor.
- `supabase_kms.py` — `SupabaseKMS` adapter: `whoami`, `ensure_schema`,
  `create_page` (now: insert row), `search` (now: call `search_notes` RPC),
  `get_bootstrap` / `set_bootstrap`. `supabase` is imported lazily so the module
  is importable without the package installed.
- `search_notes(query_text, max_results)` — PL/pgSQL function. Returns up to
  `max_results` rows ranked by `ts_rank`. RLS-friendly: respects the JWT in
  the anon key (we only use the anon key, so policy reads as
  "anyone with the anon key can read/write notes" — appropriate for a single-
  user vault).
- `envfile.py` v4 schema + `detect_backend(path)` helper + `strict_v4` kwarg
  on `read()`. v4 callers get a migration error on encountering v3 keys; v3
  callers see no behavior change.
- `flows.py` — all four handlers (`init` / `take` / `retrieve` / `whoami`) now
  dispatch to `SupabaseKMS`. Stdout / stderr strings updated: `"Notion API …"`
  → `"Supabase API …"`; `"skill-secret-vault"` → `"<project_ref>"`; `"Page id …"`
  → `"Row id …"`; `MODE: notion` → `MODE: supabase`.
- `secret.py` — `init` subparser takes `--url --api-key --password`. Help text
  and v4 docstring.

### Removed

- `notion_kms.py` (239 lines) and its `NotionKMS` adapter.
- `test_notion_kms.py` (14 tests).
- `notion-client` dependency from `requirements.txt`.
- "Dependency modes" SKILL.md section (already gone after v3 — confirmed not
  reintroduced for v4).
- v3 "v2 → v3 migration" section in `README.md` was rewritten in place as
  "v3 → v4 migration" (no separate "Migrating from v3" page).

### Security

- **PBKDF2-HMAC-SHA256, 720,000 iterations** for key derivation of the anon
  key blob (unchanged from v3; OWASP 2024). Per-blob 16-byte random salt.
- **Fernet (AES-128-CBC + HMAC-SHA256)** wraps the anon key. Self-contained
  blob: `[16B salt | 4B iters LE | Fernet token]`.
- **The Supabase anon key is the only secret in `.env`.** It is encrypted at
  rest in `.env`. In production, the agent never sees the plaintext key in
  conversation context — only the result of Supabase API calls is observable.
- **Row-Level Security caveat.** RLS on `notes` is enabled but open to the
  anon role by default in the shipped `setup.sql` (single-user vault). If you
  harden the project to multi-user, add an RLS policy keyed on
  `auth.uid()` and switch the client to the user's JWT instead of the anon
  key.
- **Compromised-Supabase-project threat.** Anyone with both your project URL
  and the anon key can read and write your notes. v3 had the same property
  with the Notion token. Treat the anon key with the same care as the Notion
  token in v3.
- **`.env` is `0o600`**, written atomically via `os.open(..., 0o600)` +
  `fsync` + `os.replace`. `.env` is in `.gitignore`.
- **No password recovery.** Forgetting the password means re-running `init`
  against a fresh Supabase project; old rows remain in Postgres but are
  unreachable without the password.
- **Network: only outbound to `<ref>.supabase.co` over HTTPS** via
  `supabase-py` (uses `httpx` under the hood). No other endpoints.

### Exit codes

- `0` — operation succeeded (including "no confident match"; this is a normal
  outcome for `retrieve`).
- `2` — wrong password, or bad CLI arguments (includes the new
  `--url must be https://<ref>.supabase.co` and `--api-key must be a JWT`
  validations on `init`).
- `3` — Supabase API error (or init-time setup error, including the new
  `ERROR: Could not verify schema: <reason>` from `ensure_schema`).
- `4` — not initialized (`.env` missing or unreadable, **or the `.env` is
  from v3 and needs migration**).
- `5` — already initialized (re-init blocked).

### Migration from v3

There is no in-place migration tool by design. To move forward:

1. While you still have access to your v3 Notion vault, export its contents
   via the v3 `bin/secret retrieve --query <title-fragment>` loop (or via the
   Notion UI).
2. Create a fresh Supabase project (free tier is fine for a personal vault).
3. Paste `setup.sql` into the Supabase SQL editor once. Confirm the `notes`
   table exists and the `search_notes` function is callable.
4. From the Supabase dashboard, copy the project URL (`Settings → API`) and
   the `anon` / `public` key.
5. Run `bin/secret init --url <url> --api-key *** --password ***` to write
   the new v4 `.env`. The old v3 `.env` is no longer read.
6. For each v3 note, run `bin/secret take --password *** --content <note>`.
7. Delete the old Notion database and the v3 `.env` after confirming v4
   works.

See the README "v3 → v4 migration" section for the full walkthrough.

### Test totals

- 68 tests pass (was 61 in v3.0.0; net **+7** from the v4-specific
  `test_supabase_kms.py` (14 tests) minus the removed 14 `test_notion_kms.py`
  tests, plus 7 added in v4 batch 1 for the `envfile.py` v4 schema /
  `detect_backend` / `strict_v4` path).

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

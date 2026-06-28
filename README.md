# skill-secret — Secret Courier Vault (v4)

A small CLI that stores password-protected notes in a Supabase Postgres
table (created via `setup.sql`) and returns the single best-matching
note in response to a natural-language query. Search runs server-side
via the `search_notes(...)` Postgres function; the script never decrypts
note bodies into the local process.

## What's new in v4

- **Supabase KMS replaces the v3 backend.** Notes live as rows in a
  `notes` Postgres table created by the SQL in `setup.sql`. The script
  no longer talks to any other provider.
- **Encrypted-at-rest anon key.** Supabase anon key encrypted under
  password, stored as base64 blob in `.env` next to cwd.
- **Server-side search via Postgres FTS.** `retrieve` calls the
  `search_notes(query_text, max_results)` SQL function and prints only
  the top-1 row's body. Other rows are never read into the agent's
  context.
- **No semantic stack remains.** v3 already dropped it; v4 doesn't
  change this.
- **New `.env` schema.** Three keys: `SKILL_SECRET_KMS_BACKEND=supabase`,
  `SKILL_SECRET_KMS_PROJECT_URL`, `SKILL_SECRET_KMS_API_BLOB`. A v3
  `.env` (containing `SKILL_SECRET_KMS_DB_ID` /
  `SKILL_SECRET_KMS_PARENT_PAGE_ID`) is detected and rejected with exit
  4 — see "v3 → v4 migration".

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python 3.9+.

You also need a Supabase project. Sign up at <https://supabase.com>,
create a new project, then from **Settings → API** copy the **Project
URL** (`https://abcdefghij.supabase.co`) and the **`anon` public** key.
In the Supabase dashboard's **SQL Editor**, paste the contents of
`setup.sql` (shipped at the repo root) and run it once. This creates
the `notes` table, the `search_notes(...)` function, and the supporting
index. **This is a one-time manual step** — `init` will not auto-migrate.

## First run

```bash
bin/secret init \
    --url "https://abcdefghij.supabase.co" \
    --api-key "***" \
    --password "KeepItSecret99"
```

`init` will:

1. Verify the anon key by calling `auth.get_user(jwt=…)`.
2. Verify the `notes` table exists (`ensure_schema()`). If you skipped
   the `setup.sql` step this fails with `ERROR: Could not verify
   schema: …` (exit 3) and `.env` is not written.
3. Encrypt the anon key under your password (PBKDF2-HMAC-SHA256, 720k
   iters, 16-byte salt) and base64-encode the blob.
4. Write `.env` (mode `0o600`) in the current directory containing the
   backend marker (`SKILL_SECRET_KMS_BACKEND=supabase`), the project
   URL, and the encrypted key blob.

The same `.env` is read by every subsequent command. If you `cd` to a
different directory, pass `--env-file PATH` (or set `$SKILL_SECRET_ENV`)
to point the script at the right file.

## Usage

### Examples

**1. Store a note.**

```bash
bin/secret take \
    --password "KeepItSecret99" \
    --content "The physical keys are hidden under the fake rock in the garden"
```

Agent sees on stdout: `SUCCESS: Stored. Row id <uuid>.`

**2. Store another note.**

```bash
bin/secret take \
    --password "KeepItSecret99" \
    --content "Wi-Fi password is Guest2026 and the router is in the closet"
```

**3. Retrieve the top match for a query.**

```bash
bin/secret retrieve \
    --password "KeepItSecret99" \
    --query "Where are the keys?"
```

Agent receives on stdout:

```
--- MATCH FOUND ---
The physical keys are hidden under the fake rock in the garden
```

Agent also receives on stderr: `MODE: supabase`. **The Wi-Fi password
note is never decrypted into the agent's context** — the script
deliberately returns only the single best-matching note. This is the
privacy property the skill is built around.

**4. Show sanitized account info.**

```bash
bin/secret whoami \
    --password "KeepItSecret99"
```

Agent receives on stdout:

```
--- ACCOUNT ---
project: https://abcdefghij.supabase.co
anon_key: eyJhbGci…
```

No credential material is printed (the `anon_key` line shows only the
first 8 characters of the anon key, followed by an ellipsis).

**5. Use a non-default `.env` location.**

```bash
bin/secret --env-file ~/.env.work retrieve \
    --password "KeepItSecret99" \
    --query "VPN credentials"
```

**6. No match.**

```bash
bin/secret retrieve \
    --password "KeepItSecret99" \
    --query "What is the nuclear launch code?"
```

Agent receives on stdout: `No highly relevant information found matching
those parameters.` Exit code is `0` — "no confident match" is a normal
outcome, not an error.

**7. Wrong password.**

```bash
bin/secret retrieve \
    --password "oops" \
    --query "keys"
```

Agent receives on stdout: `ERROR: Wrong password.` (exit code `2`).
The `.env` on disk is unchanged.

## CLI reference

| Command | Required flags | Purpose |
|---|---|---|
| `init` | `--url`, `--api-key`, `--password` | Create the KMS `notes` row + write `.env`. |
| `take` | `--password`, `--content` | Store one note. |
| `retrieve` | `--password`, `--query` | Fetch the top-1 matching note (server-side search). |
| `whoami` | `--password` | Print sanitized account info (`project_url`, `anon_key_id`, `region`). |
| `--env-file PATH` | (top-level) | Override the `.env` lookup. |

## Security

### API key blob format (`.env`)

The Supabase anon key is stored at `.env` as three keys:

| Key | Contents |
|---|---|
| `SKILL_SECRET_KMS_BACKEND` | Always `supabase` for v4. |
| `SKILL_SECRET_KMS_PROJECT_URL` | The Supabase project URL (`https://<ref>.supabase.co`). |
| `SKILL_SECRET_KMS_API_BLOB` | Base64 of `salt(16) \| iters(4,LE u32) \| Fernet(token)`. |

The blob is self-describing: salt and iteration count live inside the
blob, so `.env` does not need to keep them in sync. The backend marker
is what the script reads first to decide which KMS adapter to load.

### Cryptographic properties

- **Key derivation**: PBKDF2-HMAC-SHA256, 720,000 iterations, 16-byte
  random per-init salt.
- **Token encryption**: Fernet (AES-128-CBC + HMAC-SHA256). Tampering with
  any byte of the blob is detected at decrypt time and surfaces as
  `ERROR: Wrong password.`
- **File permissions**: `.env` is written with mode `0o600`. Its parent
  directory is `0o700` if the script created it.
- **No network from the local process beyond the Supabase API call.** The
  script does not phone home, does not phone anywhere else.

### Threat model

This is a personal vault against **casual snooping** — a coworker
glancing at your disk, an unattended laptop, an accidental `git add
.env`. It is *not* designed to resist:

- A determined attacker with disk images and offline cracking budget
  (use a stronger password and/or a hardware-bound key manager).
- Memory dumps of a running process that capture the in-memory master
  key (Fernet keys live in process memory for the duration of a
  decrypt call).
- Nation-state / legal compulsion (out of scope).
- **Compromised Supabase project.** The anon key is constrained by RLS;
  do not enable public read/write on the `notes` table beyond what the
  integration needs. If the anon key leaks, rotate it in the Supabase
  dashboard and re-run `init`.

If you need protection against any of the above, this is the wrong tool.

### What the script deliberately will *not* do

- It will not echo a list of all notes, a full database dump, or a
  `--list` command. The only retrieval output is the single top match.
- It will not log the password, the plaintext Supabase anon key, or the
  master key.
- It will not call any network endpoint other than the Supabase API
  (only outbound to `*.supabase.co` over HTTPS via the `supabase` SDK).
- It will not read or write v3's vault. v4 is a fresh codebase; see
  "v3 → v4 migration" below.

## Testing

```bash
.venv/bin/python -m unittest discover -v
```

The suite uses only stdlib `unittest` — no pytest, no network calls in
the tests themselves (Supabase calls are exercised via a mock client in
`test_supabase_kms.py`). 68 tests across 7 files:

- `test_crypto.py` — Fernet round-trip, salt uniqueness, tamper
  detection, iters preserved in blob.
- `test_envfile.py` — `.env` read / write / required-keys, mode bits,
  atomic write.
- `test_supabase_kms.py` — Supabase client behavior with a recorded mock
  transport.
- `test_init_flow.py`, `test_take_flow.py`,
  `test_retrieve_flow.py`, `test_whoami_flow.py` — end-to-end CLI
  flows via `flows.handle_*`, with a mocked Supabase client.

## v3 → v4 migration

There is **no automatic migration tool**. v4 has dropped the v3
v3 database-backend format entirely; `init` now provisions a Supabase
`notes` table (via `setup.sql`) and writes a v4 `.env` with three keys:
`SKILL_SECRET_KMS_BACKEND=supabase`, `SKILL_SECRET_KMS_PROJECT_URL`,
and `SKILL_SECRET_KMS_API_BLOB`. If you have notes in a v3 KMS and
want them in a v4 KMS:

1. Keep a copy of the v3 `secret.py` (or check out the `v3` tag).
2. `retrieve` each note out of your v3 KMS with the v3 script into a
   plain text file.
3. Sign up for Supabase, create a project, paste `setup.sql` into the
   SQL editor, and run it once.
4. `init` a v4 KMS against the new project.
5. `take` each chunk into the new KMS:

   ```bash
   while IFS= read -r chunk; do
       bin/secret take --password "$NEW_PW" --content "$chunk"
   done < chunks.txt
   ```

A v3 `.env` (containing `SKILL_SECRET_KMS_DB_ID` or
`SKILL_SECRET_KMS_PARENT_PAGE_ID` without
`SKILL_SECRET_KMS_BACKEND`) is detected on read and rejected with exit
code 4 — the script will not silently load it as a v4 vault. Keeping a
v3 code path inside v4 would be a permanent source of bugs and an
additional attack surface for a format that is no longer in active use,
so the v3 reader was deleted outright.
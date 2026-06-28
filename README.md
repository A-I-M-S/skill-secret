# skill-secret — Secret Courier Vault (v3)

A small CLI that stores password-protected notes in a private Notion
database and returns the single best-matching note in response to a
natural-language query. Search runs server-side on the Notion side; the
script never decrypts note bodies into the local process.

## What's new in v3

- **Notion KMS instead of a local vault file.** Notes live as pages in a
  Notion database (`skill-secret-vault`) under a parent page the user
  supplies. The script no longer writes or reads `vault.enc`.
- **Encrypted-at-rest Notion token.** The Notion integration token is
  encrypted under the user's password (PBKDF2-HMAC-SHA256, 720,000
  iterations, 16-byte salt) and stored as a base64 ciphertext blob in
  `.env` next to the working directory. The plaintext token is never
  written to disk.
- **Server-side search.** `retrieve` asks Notion for the top match and
  prints only that page's body. Other pages are never read into the
  agent's context.
- **No semantic / keyword stack.** v2's `sentence-transformers` / `torch`
  / `numpy` dependency block is gone. Search quality is whatever Notion
  provides.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python 3.9+.

You also need a Notion internal integration. Create one at
<https://www.notion.so/profile/integrations> and copy its "Internal
Integration Secret" token. The integration must have access to the
parent page you intend to use as the KMS root — share that page with
the integration in the Notion UI before running `init`.

## First run

```bash
bin/secret init \
    --notion-token "secret_xxxxxxxxxxxxxxxxxxxx" \
    --parent-page-id "00000000-0000-0000-0000-000000000000" \
    --password "KeepItSecret99"
```

`init` will:

1. Verify the Notion token by calling `/v1/users/me`.
2. Create a Notion database titled `skill-secret-vault` under the parent
   page.
3. Encrypt the Notion token under your password (PBKDF2-HMAC-SHA256,
   720k iters, 16-byte salt) and base64-encode the blob.
4. Write `.env` (mode `0o600`) in the current directory containing the
   database id, the parent page id, and the encrypted token.

**Caveat**: the parent page must be shared with the integration before
`init` runs. If the page is not shared, Notion returns
`object_not_found` and `init` exits with code 3.

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

Agent sees on stdout: `SUCCESS: Stored. Page id <uuid>.`

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

Agent also receives on stderr: `MODE: notion`. **The Wi-Fi password note
is never decrypted into the agent's context** — the script deliberately
returns only the single best-matching note. This is the privacy property
the skill is built around.

**4. Show sanitized account info.**

```bash
bin/secret whoami \
    --password "KeepItSecret99"
```

Agent receives on stdout:

```
--- ACCOUNT ---
bot_id: <uuid>
workspace: <workspace name>
```

No credential material is printed.

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
| `init` | `--notion-token`, `--parent-page-id`, `--password` | Create the KMS database and write `.env`. |
| `take` | `--password`, `--content` | Store one note. |
| `retrieve` | `--password`, `--query` | Fetch the top-1 matching note (server-side search). |
| `whoami` | `--password` | Print sanitized account info (`bot_id`, `workspace`). |
| `--env-file PATH` | (top-level) | Override the `.env` lookup. |

## Security

### API key blob format (`.env`)

The Notion integration token is stored at `.env` as three keys:

| Key | Contents |
|---|---|
| `SKILL_SECRET_KMS_DB_ID` | The Notion database id (UUID). |
| `SKILL_SECRET_KMS_PARENT_PAGE_ID` | The Notion parent page id (UUID). |
| `SKILL_SECRET_KMS_API_BLOB` | Base64 of `salt(16) \| iters(4,LE u32) \| Fernet(token)`. |

The blob is self-describing: salt and iteration count live inside the
blob, so `.env` does not need to keep them in sync.

### Cryptographic properties

- **Key derivation**: PBKDF2-HMAC-SHA256, 720,000 iterations, 16-byte
  random per-init salt.
- **Token encryption**: Fernet (AES-128-CBC + HMAC-SHA256). Tampering with
  any byte of the blob is detected at decrypt time and surfaces as
  `ERROR: Wrong password.`
- **File permissions**: `.env` is written with mode `0o600`. Its parent
  directory is `0o700` if the script created it.
- **No network from the local process beyond the Notion API call.** The
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

If you need protection against any of the above, this is the wrong tool.

### What the script deliberately will *not* do

- It will not echo a list of all notes, a full database dump, or a
  `--list` command. The only retrieval output is the single top match.
- It will not log the password, the plaintext Notion token, or the
  master key.
- It will not call any network endpoint other than the Notion API.
- It will not read or write v2's `vault.enc` format. v3 is a fresh
  codebase; see "v2 → v3 migration" below.

## Testing

```bash
.venv/bin/python -m unittest discover -v
```

The suite uses only stdlib `unittest` — no pytest, no network calls in
the tests themselves (Notion calls are exercised via a mock client in
`test_notion_kms.py`). 61 tests across 6 files:

- `test_crypto.py` — Fernet round-trip, salt uniqueness, tamper
  detection, iters preserved in blob.
- `test_envfile.py` — `.env` read / write / required-keys, mode bits,
  atomic write.
- `test_notion_kms.py` — Notion client behavior with a recorded mock
  transport.
- `test_init_flow.py`, `test_take_flow.py`,
  `test_retrieve_flow.py`, `test_whoami_flow.py` — end-to-end CLI
  flows via `flows.handle_*`, with a mocked Notion client.

## v2 → v3 migration

There is **no automatic migration tool**. v3 has dropped the local
`vault.enc` format entirely; the v2 encrypt / decrypt subcommands no
longer exist. If you have notes in a v2 vault and want them in a v3
KMS:

1. Keep a copy of the v2 `secret.py` (or check out the `v2` tag).
2. Decrypt each chunk out of `vault.enc` with the v2 script.
3. `init` a v3 KMS against a fresh parent page.
4. `take` each chunk into the new KMS:

   ```bash
   while IFS= read -r chunk; do
       bin/secret take --password "$NEW_PW" --content "$chunk"
   done < chunks.txt
   ```

Keeping a v2 code path inside v3 would be a permanent source of bugs
and an additional attack surface for a format that is no longer in
active use, so the v2 reader was deleted outright.

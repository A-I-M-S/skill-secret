---
name: skill-secret
description: Manages an encrypted document vault. Stores password-protected content and returns the single best-matching chunk in response to a natural-language query. Runs locally with no network access.
---

# Secret Courier Vault

Use this skill to encrypt new information into a password-protected vault and
to semantically (or by keyword) search through existing vaults. You must
always invoke the `secret.py` script via your execution tool. The script
returns at most one matching chunk per query; the rest of the vault is
hidden from your session.

## 🛡️ CORE RULES

1. **Never reveal passwords** in your responses. The user provides the
   password; you pass it via `--password` and never echo it.
2. **Never output the entire decrypted contents** of a file. Only return the
   targeted answer from a search. There is intentionally no `--list` or
   `--dump` flag.

## 🛠️ Commands

### 1. Storing / appending information

```
python3 secret.py encrypt \
    --password "<password>" \
    --file "<filename>" \
    --content "<content>"
```

Behavior:
- If the file does not exist, it is created and the content is stored as
  chunk 0.
- If the file exists, the password is verified (by authenticating the
  header MAC and the first chunk) and the content is appended as a new
  chunk. The existing salt and iteration count are preserved.

Success responses (stdout, one line):
- `SUCCESS: Stored 1 chunk.`
- `SUCCESS: Appended. Vault now has N chunks.`

### 2. Searching information

```
python3 secret.py decrypt \
    --password "<password>" \
    --file "<filename>" \
    --query "<search_parameters>" \
    [--mode {auto,semantic,keyword}] \
    [--threshold FLOAT] \
    [--top-k INT]
```

Flags:
- `--mode` (default `auto`): `auto` picks semantic if the model is
  importable, else keyword. Use `semantic` to force the ML model or
  `keyword` to force the BM25-lite fallback.
- `--threshold` (default `0.30` for semantic, `0.05` for keyword): minimum
  score to return a match. Lower = more permissive.
- `--top-k` (default `1`): number of chunks to return. Default is 1; the
  script will not dump the whole vault regardless of this value.

Search responses (stdout):
- Match: `--- MATCH FOUND ---\n<chunk plaintext>`
- No confident match: `No highly relevant information found matching those parameters.`
- Empty vault: `Vault is empty.`

Mode banner (stderr, one line, on every successful search):
- `MODE: semantic` or `MODE: keyword`

## 🚨 Error paths

All errors are printed to stdout as a single `ERROR:` line. Match the string
verbatim to map to a user-facing message.

| Condition | stdout | Exit |
|---|---|---|
| Wrong password on `decrypt` | `ERROR: Wrong password.` | 2 |
| Wrong password on `encrypt` (append) | `ERROR: Wrong password. Append rejected.` | 2 |
| File missing | `ERROR: Vault file does not exist: <path>` | 4 |
| Header / file structurally corrupt | `ERROR: Vault file is corrupt or tampered (header).` | 3 |
| All chunks unreadable (every chunk failed decryption) | `ERROR: Vault file is corrupt or tampered (no readable chunks).` | 3 |
| Bad CLI arguments | argparse usage to stderr | 2 |
| Unexpected internal error | `ERROR: Internal error: <type>: <msg>` | 1 |

Per-chunk corruption is **not** a fatal error: a single bad chunk is skipped,
a `WARN: chunk <N> unreadable, skipped` line is written to stderr, and the
remaining chunks are searched. This is by design — partial recovery is
preferable to total loss for a personal vault.

## 🔄 Dependency modes

The skill works in two modes, chosen automatically based on what is
installed:

- **Semantic mode**: uses `sentence-transformers` with the
  `all-MiniLM-L6-v2` model. Cosine similarity over sentence embeddings.
  Better at paraphrased queries, slower to start, requires ~100 MB of
  dependencies (torch, numpy, transformers).
- **Keyword mode**: BM25-lite scorer with a small built-in stopword list.
  Fast, zero extra dependencies beyond `cryptography`. Less forgiving of
  paraphrase — exact words matter.

If the model is unavailable at runtime and `--mode semantic` was forced, the
script logs `WARN: sentence-transformers unavailable, falling back to
keyword mode.` to stderr and continues in keyword mode with a `MODE: keyword`
banner.

## 🔒 Upgraded security properties (v2)

- **PBKDF2-HMAC-SHA256, 720,000 iterations** for key derivation (was raw
  SHA-256, no salt, no work factor). This matches the OWASP 2024
  recommendation and is brute-force resistant against casual attackers.
- **Per-vault 16-byte random salt.** Two vaults encrypted with the same
  password produce different ciphertext.
- **Per-chunk authenticated encryption.** Each chunk is a Fernet token
  (AES-128-CBC + HMAC-SHA256). Tampering with any chunk is detected at
  decrypt time; that chunk is skipped and the rest remain searchable.
- **Header MAC.** A 16-byte HMAC-SHA256 over the header fields,
  keyed by the password. Wrong password fails this check before any chunk
  is touched, so an attacker cannot distinguish wrong-password from a
  real header corruption in a probing attack.
- **Atomic writes.** Encrypts write to `<filename>.tmp`, `fsync`, then
  `os.replace`. Power loss mid-write cannot leave a half-written vault.
- **Local-only.** No network calls. The sentence-transformers model is
  loaded from the local cache.

## 🧪 Exit-code summary

- `0` — operation succeeded (including "no confident match" and "empty
  vault"; these are not errors, they are normal outcomes)
- `1` — unexpected internal error
- `2` — wrong password, or bad CLI arguments
- `3` — vault file corrupt or tampered
- `4` — vault file does not exist

Exit code 0 does **not** mean a match was found. Always read stdout and
pattern-match on the specific success/failure strings listed above.

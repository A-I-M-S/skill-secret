# skill-secret — Secret Courier Vault

A small, local-only encrypted vault. You write secrets into a password-protected
file, then later ask natural-language questions about what's in it. Only the
single best-matching chunk is ever returned — the rest of the vault stays
hidden from the caller.

This is the v2 release. It introduces per-vault salts, PBKDF2-HMAC-SHA256 key
derivation at 720,000 iterations, per-chunk authenticated encryption, and a
graceful keyword-search fallback when the heavy ML stack is not installed.

---

## Setup

```bash
# Full install (semantic + keyword modes)
pip install -r requirements.txt

# Keyword-only install (no torch / sentence-transformers):
#   Comment out the "# --- semantic mode stack ---" block in
#   requirements.txt, then:
pip install -r requirements.txt
```

Requires Python 3.9+.

`cryptography` is always required. The sentence-transformers stack (torch,
numpy, transformers, etc.) is only needed for semantic search; the script
auto-detects its presence and falls back to a BM25-lite keyword scorer.

---

## Usage

### Examples

These are the canonical flows. The agent should follow them literally.

**1. Person A saves a secret.**

> User: "Agent, save 'The physical keys are hidden under the fake rock in the garden' to `vault.enc` using password 'KeepItSecret99'."

```bash
python3 secret.py encrypt \
    --password "KeepItSecret99" \
    --file "vault.enc" \
    --content "The physical keys are hidden under the fake rock in the garden"
```

Agent sees on stdout: `SUCCESS: Stored 1 chunk.`

**2. Person A adds another secret to the same vault later.**

> User: "Agent, append 'Wi-Fi password is Guest2026 and the router is in the closet' to `vault.enc` using password 'KeepItSecret99'."

```bash
python3 secret.py encrypt \
    --password "KeepItSecret99" \
    --file "vault.enc" \
    --content "Wi-Fi password is Guest2026 and the router is in the closet"
```

Agent sees on stdout: `SUCCESS: Appended. Vault now has 2 chunks.`

**3. Person B asks a question — only the relevant chunk is returned.**

> User: "Agent, search `vault.enc` for 'Where are the keys?' using password 'KeepItSecret99'."

```bash
python3 secret.py decrypt \
    --password "KeepItSecret99" \
    --file "vault.enc" \
    --query "Where are the keys?"
```

Agent receives on stdout:

```
--- MATCH FOUND ---
The physical keys are hidden under the fake rock in the garden
```

Agent also receives on stderr: `MODE: keyword` (or `MODE: semantic` if the ML model is installed).

The agent returns the matched chunk to the user. **The Wi-Fi password chunk is never decrypted into the agent's context** — the script deliberately returns only the single best-matching chunk. This is the privacy property the skill is built around.

**4. Force the keyword mode (no ML model required).**

If `sentence-transformers` is not installed, `--mode auto` will already pick keyword. To force it explicitly — or to skip the startup cost of the ML model on a slow machine — pass `--mode keyword`:

```bash
python3 secret.py decrypt \
    --password "KeepItSecret99" \
    --file "vault.enc" \
    --query "wifi credentials" \
    --mode keyword
```

The `--threshold` flag can be lowered (e.g. `--threshold 0.01`) for a more permissive match.

**5. The search finds nothing.**

> User: "Agent, search `vault.enc` for 'What is the nuclear launch code?' using password 'KeepItSecret99'."

```bash
python3 secret.py decrypt \
    --password "KeepItSecret99" \
    --file "vault.enc" \
    --query "What is the nuclear launch code?"
```

Agent receives on stdout: `No highly relevant information found matching those parameters.`

Exit code is still `0` — "no confident match" is a normal outcome, not an error. The agent should tell the user the vault does not contain anything matching, not interpret this as a failure.

**6. The wrong password is rejected.**

> User: "Agent, search `vault.enc` for 'keys' using password 'oops'."

```bash
python3 secret.py decrypt \
    --password "oops" \
    --file "vault.enc" \
    --query "keys"
```

Agent receives on stdout: `ERROR: Wrong password.` (exit code `2`). The agent should tell the user the password was rejected and ask them to try again. The vault on disk is unchanged.

---

### Create or append to a vault

```bash
python3 secret.py encrypt \
    --password "KeepItSecret99" \
    --file "vault.enc" \
    --content "The physical keys are hidden under the fake rock in the garden"
```

If `vault.enc` does not exist, it is created. If it exists, the password is
verified and the new content is appended as a new chunk. The existing salt and
iteration count are preserved on append.

### Search a vault

```bash
python3 secret.py decrypt \
    --password "KeepItSecret99" \
    --file "vault.enc" \
    --query "Where are the keys?"
```

Returns the single most relevant chunk, or "No highly relevant information
found matching those parameters." The mode used (semantic or keyword) is
written to stderr as `MODE: semantic` or `MODE: keyword`.

### Search flags

| Flag | Default | Purpose |
|---|---|---|
| `--mode {auto,semantic,keyword}` | `auto` | Force a search mode. `auto` picks semantic if the model is importable, else keyword. |
| `--threshold FLOAT` | `0.30` semantic, `0.05` keyword | Minimum score to return a match. Lower = more permissive. |
| `--top-k INT` | `1` | Reserved for returning more chunks. Default preserves v1 behavior (one chunk only). |

### Output channels

- **stdout**: the answer (a single chunk, an empty-vault message, a no-match message, or a single-line `ERROR:` for failure).
- **stderr**: mode banner (`MODE: ...`), per-chunk corruption warnings (`WARN: chunk N unreadable, skipped`), and the rare model-fallback warning.

Agents should pattern-match on stdout, never echo `--password` back, and never
call the script in a way that would dump the whole vault (no such flag exists
by design).

---

## Security

### Vault file format (VLT1)

| Offset | Size | Field |
|---|---|---|
| 0x00 | 4 | Magic `VLT1` |
| 0x04 | 1 | Version (0x02) |
| 0x05 | 1 | Flags (reserved) |
| 0x06 | 2 | Reserved |
| 0x08 | 16 | PBKDF2 salt (per-vault) |
| 0x18 | 4 | PBKDF2 iteration count (uint32 LE) |
| 0x1C | 4 | Chunk count (uint32 LE) |
| 0x20 | 16 | Header MAC (HMAC-SHA256, truncated) |
| 0x30 | 4×N | Chunk offset table (uint32 LE each, from 0x00) |
| … | varies | Concatenated chunk payloads, each: `[uint32 len][Fernet token]` |

### Cryptographic properties

- **Key derivation**: PBKDF2-HMAC-SHA256, 720,000 iterations, 16-byte random per-vault salt. Master key is 32 bytes; encoded as a Fernet key. The header MAC uses a separate key derived directly from the password (no PBKDF2) so iteration upgrades are cheap.
- **Chunk encryption**: Each plaintext chunk is encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The full Fernet token is stored, including its built-in HMAC. Per-chunk tampering is detected by Fernet's own authentication tag.
- **Per-chunk isolation**: If one chunk's ciphertext or tag is corrupted, that chunk is skipped with a `WARN: chunk N unreadable, skipped` on stderr and the other chunks remain searchable.
- **Atomic writes**: Encrypts write to `<file>.tmp`, `fsync`, then `os.replace`. A crash mid-write leaves the previous vault intact.
- **No network**: All operations are local. No telemetry, no model download at search time (the model is loaded from the local sentence-transformers cache).
- **No password recovery**: Forgetting the password means losing the vault. There is no backdoor and there cannot be one in this design.

### Threat model

This is a personal vault against **casual snooping** — a coworker glancing at
your disk, an unattended laptop, an accidental `git add vault.enc`. It is
*not* designed to resist:

- A determined attacker with disk images and offline cracking budget (use a
  stronger password and/or a hardware-bound key manager).
- Memory dumps of a running process that capture the in-memory master key
  (Fernet keys live in process memory for the duration of a decrypt call).
- Nation-state / legal compulsion (out of scope).

If you need protection against any of the above, this is the wrong tool.

### What the script deliberately will *not* do

- It will not echo a list of all chunks, a full vault dump, or a `--list`
  command. The only decryption output is the top-K (default 1) search hit.
- It will not log the password, the plaintext, or the master key.
- It will not read v1 (single-blob, SHA-256) vaults. See "Migrating from v0.01"
  below.

---

## Testing

```bash
python3 -m unittest test_secret.py -v
```

The suite uses only stdlib `unittest` — no pytest, no network. The two
semantic-mode tests are automatically **skipped** when `sentence_transformers`
is not installed, so the suite passes in both full and keyword-only
environments. The remaining 15 tests exercise the format, the chunk-isolation
behavior, the atomic-write property, the error taxonomy, the keyword scorer,
and the salt uniqueness invariant.

---

## Migrating from v0.01

The old format (single Fernet blob, raw SHA-256 key derivation) is **not**
readable by this version. To migrate:

1. Keep a copy of the old `secret.py` (or check out the `v0.01` tag).
2. Decrypt your old vault with the old script and a temporary file.
3. Re-encrypt the contents with this version:

   ```bash
   for chunk in $chunks; do
       python3 secret.py encrypt --password "$PW" --file vault.enc --content "$chunk"
   done
   ```

There is no automatic migration tool, by design — keeping a v0.01 code path
inside v2 would be a permanent source of bugs and an additional attack
surface for a format that is not in active use.

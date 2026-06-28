# skill-secret: Secret Courier Vault
# Local-only encrypted vault. No network calls.

import argparse
import base64
import hashlib
import hmac
import os
import secrets
import struct
import sys

from cryptography.fernet import Fernet, InvalidToken


MAGIC = b"VLT1"
VERSION = 2
HEADER_SIZE = 48            # magic(4) + ver(1) + flags(1) + reserved(2) + salt(16) + iters(4) + count(4) + hdr_mac(16)
SALT_SIZE = 16
HDR_MAC_SIZE = 16
DEFAULT_PBKDF2_ITERS = 720_000
SEMANTIC_THRESHOLD = 0.30
KEYWORD_THRESHOLD = 0.05
TOP_K_DEFAULT = 1
HEADER_MAC_KEY = b"skill-secret-header-v1"

# Tiny built-in English stopword set. Deliberately small so non-English
# vaults aren't blindsided by a long list of "the/is/and" the user
# didn't ask for. Extend here if you want.
STOPWORDS = frozenset(
    "a an the and or but if then else for to of in on at by from with "
    "as is are was were be been being have has had do does did this that "
    "these those it its their there here what which who whom whose how "
    "when where why can could should would will may might shall must not "
    "no nor so than too very also just only into about over under up down "
    "out off such i me my mine you your yours he him his she her hers we "
    "us our ours they them theirs".split()
)


def _err(msg):
    print(msg)
    sys.exit(2)


def derive_key(password: str, salt: bytes, iters: int = DEFAULT_PBKDF2_ITERS) -> bytes:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, 32)
    return base64.urlsafe_b64encode(raw)


def _compute_header_mac(password: str, salt: bytes, iters: int, count: int) -> bytes:
    msg = struct.pack("<16s I I", salt, iters, count)
    return hmac.new(HEADER_MAC_KEY, msg + password.encode("utf-8"), hashlib.sha256).digest()[:HDR_MAC_SIZE]


def encode_header(password: str, salt: bytes, iters: int, offsets):
    head_no_mac = struct.pack(
        "<4sBBH 16s I I",
        MAGIC,
        VERSION,
        0,                 # flags
        0,                 # reserved
        salt,
        iters,
        len(offsets),
    )
    mac = _compute_header_mac(password, salt, iters, len(offsets))
    return head_no_mac + mac + struct.pack(f"<{len(offsets)}I", *offsets)


def decode_header_and_verify(data: bytes, password: str):
    if len(data) < HEADER_SIZE:
        raise ValueError("header too short")
    magic, ver, _flags, _reserved, salt, iters, count = struct.unpack(
        "<4sBBH 16s I I", data[:32]
    )
    if magic != MAGIC:
        raise ValueError("bad magic")
    if ver != VERSION:
        raise ValueError("unsupported version")
    if len(salt) != SALT_SIZE or iters < 1 or count < 0:
        raise ValueError("bad header fields")
    stored_mac = data[32:32 + HDR_MAC_SIZE]
    expected = _compute_header_mac(password, salt, iters, count)
    if not hmac.compare_digest(stored_mac, expected):
        # Wrong password OR tampered header bytes -- caller cannot tell which
        # from the MAC alone, so we surface it as "wrong password" since that's
        # the overwhelmingly common case.
        raise InvalidToken("header MAC mismatch")
    index_size = 4 * count
    if len(data) < HEADER_SIZE + index_size:
        raise ValueError("truncated index")
    offsets = list(struct.unpack(f"<{count}I", data[HEADER_SIZE:HEADER_SIZE + index_size]))
    return salt, iters, offsets


def decode_header(data: bytes):
    """Unverified header decode -- used by the encrypt append path which
    already authenticated via read_vault. Returns (salt, iters, offsets)
    without checking the MAC. Raises ValueError on structural problems."""
    if len(data) < HEADER_SIZE:
        raise ValueError("header too short")
    magic, ver, _flags, _reserved, salt, iters, count = struct.unpack(
        "<4sBBH 16s I I", data[:32]
    )
    if magic != MAGIC:
        raise ValueError("bad magic")
    if ver != VERSION:
        raise ValueError("unsupported version")
    if len(salt) != SALT_SIZE or iters < 1 or count < 0:
        raise ValueError("bad header fields")
    index_size = 4 * count
    if len(data) < HEADER_SIZE + index_size:
        raise ValueError("truncated index")
    offsets = list(struct.unpack(f"<{count}I", data[HEADER_SIZE:HEADER_SIZE + index_size]))
    return salt, iters, offsets


def _read_chunk_token(data: bytes, offset: int):
    if offset + 4 > len(data):
        raise ValueError("chunk length past end")
    (length,) = struct.unpack("<I", data[offset:offset + 4])
    if length <= 0 or offset + 4 + length > len(data):
        raise ValueError("bad chunk length")
    return data[offset + 4:offset + 4 + length]


def _decrypt_all_chunks(data: bytes, offsets, fernet):
    plaintexts = []
    skipped = []
    for i, off in enumerate(offsets):
        try:
            token = _read_chunk_token(data, off)
            pt = fernet.decrypt(token).decode("utf-8")
            plaintexts.append((i, pt))
        except (InvalidToken, ValueError):
            skipped.append(i)
            plaintexts.append((i, None))
    return plaintexts, skipped


def write_vault(path: str, password: str, plaintexts, existing_salt=None,
                existing_iters=None):
    salt = existing_salt if existing_salt is not None else secrets.token_bytes(SALT_SIZE)
    iters = existing_iters if existing_iters is not None else DEFAULT_PBKDF2_ITERS
    key = derive_key(password, salt, iters)
    f = Fernet(key)

    blobs = []
    for pt in plaintexts:
        token = f.encrypt(pt.encode("utf-8"))
        blobs.append(struct.pack("<I", len(token)) + token)

    # Compute offsets as we lay out the file in memory, then write.
    payload_start = HEADER_SIZE + 4 * len(blobs)
    offsets = []
    cursor = payload_start
    for blob in blobs:
        offsets.append(cursor)
        cursor += len(blob)
    payload = b"".join(blobs)

    header = encode_header(password, salt, iters, offsets)
    blob = header + payload

    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_vault(path: str, password: str):
    with open(path, "rb") as fh:
        data = fh.read()
    salt, iters, offsets = decode_header_and_verify(data, password)
    key = derive_key(password, salt, iters)
    f = Fernet(key)
    plaintexts, skipped = _decrypt_all_chunks(data, offsets, f)
    if plaintexts and all(pt is None for _, pt in plaintexts):
        return None, skipped  # signal "all unreadable"
    return plaintexts, skipped


def _tokenize(text: str):
    out = []
    cur = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                tok = "".join(cur)
                if tok and tok not in STOPWORDS:
                    out.append(tok)
                cur = []
    if cur:
        tok = "".join(cur)
        if tok and tok not in STOPWORDS:
            out.append(tok)
    return out


def score_keyword(query: str, chunks):
    # chunks: list of (index, plaintext) with plaintext guaranteed non-None.
    q_tokens = _tokenize(query)
    if not q_tokens:
        return None, 0.0
    scored = []
    lens = [max(1, len(_tokenize(pt))) for _, pt in chunks]
    avg_len = sum(lens) / len(lens) if lens else 1.0
    for (i, pt), dl in zip(chunks, lens):
        toks = _tokenize(pt)
        if not toks:
            scored.append((i, 0.0))
            continue
        tf = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for qt in q_tokens:
            f = tf.get(qt, 0)
            if f == 0:
                continue
            # BM25-lite: tf * (k+1) / (f + k*(1 - b + b*dl/avgdl))
            # with k=1.5, b=0.75. No IDF -- small vaults, raw tf*norm is fine.
            k = 1.5
            b = 0.75
            denom = f + k * (1 - b + b * dl / avg_len)
            s += (f * (k + 1)) / denom
        scored.append((i, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0]


def _have_semantic():
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


def score_semantic(query: str, chunks, model, top_k=1):
    # chunks: list of (index, plaintext) with plaintext guaranteed non-None.
    from sentence_transformers import util
    texts = [pt for _, pt in chunks]
    q_emb = model.encode(query, convert_to_tensor=True)
    c_emb = model.encode(texts, batch_size=32, convert_to_tensor=True)
    hits = util.semantic_search(q_emb, c_emb, top_k=min(top_k, len(texts)))
    results = []
    for h in hits[0]:
        results.append((chunks[h["corpus_id"]][0], float(h["score"])))
    return results[0] if results else (None, 0.0)


def _decrypt_and_search(args, mode, threshold, top_k, write_mode_banner):
    try:
        plaintexts, skipped = read_vault(args.file, args.password)
    except FileNotFoundError:
        print(f"ERROR: Vault file does not exist: {args.file}")
        sys.exit(4)
    except InvalidToken:
        print("ERROR: Wrong password.")
        sys.exit(2)
    except ValueError:
        print("ERROR: Vault file is corrupt or tampered (header).")
        sys.exit(3)
    except OSError:
        print(f"ERROR: Vault file does not exist: {args.file}")
        sys.exit(4)

    if plaintexts is None:
        print("ERROR: Vault file is corrupt or tampered (no readable chunks).")
        sys.exit(3)

    good = [(i, pt) for (i, pt) in plaintexts if pt is not None]
    for s in skipped:
        sys.stderr.write(f"WARN: chunk {s} unreadable, skipped\n")

    if not good:
        print("ERROR: Vault file is corrupt or tampered (no readable chunks).")
        sys.exit(3)

    if not good or all(not pt.strip() for _, pt in good):
        print("Vault is empty.")
        return

    write_mode_banner(mode)

    if mode == "semantic":
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            best_idx, score = score_semantic(args.query, good, model, top_k=top_k)
        except Exception as e:
            sys.stderr.write(
                "WARN: sentence-transformers unavailable, falling back to keyword mode.\n"
            )
            best_idx, score = score_keyword(args.query, good)
            mode = "keyword"
            write_mode_banner("keyword")
    else:
        best_idx, score = score_keyword(args.query, good)

    if score < threshold or best_idx is None:
        print("No highly relevant information found matching those parameters.")
        return

    match = next(pt for i, pt in good if i == best_idx)
    print(f"--- MATCH FOUND ---\n{match}")


def handle_encrypt(args):
    new_plaintexts = [args.content]
    if os.path.exists(args.file):
        try:
            existing, _skipped = read_vault(args.file, args.password)
        except InvalidToken:
            print("ERROR: Wrong password. Append rejected.")
            sys.exit(2)
        except ValueError:
            print("ERROR: Vault file is corrupt or tampered (header).")
            sys.exit(3)
        except OSError:
            print(f"ERROR: Vault file does not exist: {args.file}")
            sys.exit(4)
        if existing is None:
            print("ERROR: Vault file is corrupt or tampered (no readable chunks).")
            sys.exit(3)
        plaintexts = [pt for _, pt in existing if pt is not None]
        dropped = [i for (i, pt) in existing if pt is None]
        for d in dropped:
            sys.stderr.write(f"WARN: chunk {d} unreadable, dropping on append\n")
        plaintexts = plaintexts + new_plaintexts
        # Preserve existing salt+iters on append.
        with open(args.file, "rb") as fh:
            data = fh.read()
        salt, iters, _ = decode_header(data)
        write_vault(args.file, args.password, plaintexts,
                    existing_salt=salt, existing_iters=iters)
        print(f"SUCCESS: Appended. Vault now has {len(plaintexts)} chunks.")
    else:
        write_vault(args.file, args.password, new_plaintexts)
        print("SUCCESS: Stored 1 chunk.")


def handle_decrypt(args):
    mode = args.mode
    threshold = args.threshold
    if mode == "auto":
        effective = "semantic" if _have_semantic() else "keyword"
    else:
        effective = mode

    def banner(m):
        sys.stderr.write(f"MODE: {m}\n")

    if threshold is None:
        threshold = SEMANTIC_THRESHOLD if effective == "semantic" else KEYWORD_THRESHOLD

    _decrypt_and_search(args, effective, threshold, args.top_k, banner)


V2_DEPRECATION = (
    "DEPRECATION: this subcommand is v2; in v3 use 'init' then "
    "'take' (or 'retrieve'). Will be removed in batch 3."
)


def _emit_v2_deprecation() -> None:
    sys.stderr.write(V2_DEPRECATION + "\n")


def handle_encrypt_v2(args) -> None:
    _emit_v2_deprecation()
    handle_encrypt(args)


def handle_decrypt_v2(args) -> None:
    _emit_v2_deprecation()
    handle_decrypt(args)


def main():
    import flows

    p = argparse.ArgumentParser(
        description="Secret Courier Vault (v3: Notion KMS)",
    )
    p.add_argument(
        "--env-file",
        default=None,
        help=(
            "Path to the .env file (overrides $SKILL_SECRET_ENV "
            "and ./$CWD/.env)."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="initialize a new KMS database")
    init_p.add_argument("--notion-token", required=True)
    init_p.add_argument("--parent-page-id", required=True)
    init_p.add_argument("--password", required=True)

    take_p = sub.add_parser("take", help="store a note in the KMS")
    take_p.add_argument("--password", required=True)
    take_p.add_argument("--content", required=True)

    retrieve_p = sub.add_parser(
        "retrieve", help="fetch the top-1 matching note from the KMS"
    )
    retrieve_p.add_argument("--password", required=True)
    retrieve_p.add_argument("--query", required=True)

    whoami_p = sub.add_parser("whoami", help="show sanitized account info")
    whoami_p.add_argument("--password", required=True)

    enc_p = sub.add_parser(
        "encrypt",
        help=argparse.SUPPRESS,
        description="v2: encrypt content into a local vault file.",
    )
    enc_p.add_argument("--password", required=True)
    enc_p.add_argument("--file", required=True)
    enc_p.add_argument("--content", required=True)

    dec_p = sub.add_parser(
        "decrypt",
        help=argparse.SUPPRESS,
        description="v2: search a local vault file for a matching chunk.",
    )
    dec_p.add_argument("--password", required=True)
    dec_p.add_argument("--file", required=True)
    dec_p.add_argument("--query", required=True)
    dec_p.add_argument(
        "--mode", choices=("auto", "semantic", "keyword"), default="auto"
    )
    dec_p.add_argument("--threshold", type=float, default=None)
    dec_p.add_argument("--top-k", type=int, default=TOP_K_DEFAULT)

    args = p.parse_args()

    if args.command == "init":
        flows.handle_init(args)
    elif args.command == "take":
        flows.handle_take(args)
    elif args.command == "retrieve":
        flows.handle_retrieve(args)
    elif args.command == "whoami":
        flows.handle_whoami(args)
    elif args.command == "encrypt":
        handle_encrypt_v2(args)
    elif args.command == "decrypt":
        handle_decrypt_v2(args)
    else:
        p.print_usage(sys.stderr)
        sys.stderr.write(
            f"secret.py: error: a subcommand is required "
            f"(init|take|retrieve|whoami)\n"
        )
        sys.exit(2)


if __name__ == "__main__":
    main()

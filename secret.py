import argparse
import os
import sys
import base64
import hashlib
from cryptography.fernet import Fernet

# Helper to turn any string password into a secure 32-byte Fernet key
def derive_key(password: str) -> bytes:
    digest = hashlib.sha256(password.encode()).digest()
    return base64.urlsafe_b64encode(digest)

def handle_encrypt(args):
    key = derive_key(args.password)
    cipher_suite = Fernet(key)
    
    # Scenario 1: File exists, handle appending
    if os.path.exists(args.file):
        try:
            with open(args.file, "rb") as f:
                encrypted_data = f.read()
            # Attempt decryption to verify password
            existing_plaintext = cipher_suite.decrypt(encrypted_data).decode('utf-8')
            # Append new content with spacing
            updated_plaintext = existing_plaintext + "\n\n" + args.content
        except Exception:
            print("ERROR: File exists but password is incorrect.")
            sys.exit(1)
            
    # Scenario 3: File does not exist, create new
    else:
        updated_plaintext = args.content

    # Encrypt and save
    encrypted_payload = cipher_suite.encrypt(updated_plaintext.encode('utf-8'))
    with open(args.file, "wb") as f:
        f.write(encrypted_payload)
    print("SUCCESS: Content encrypted and secured.")

def handle_decrypt(args):
    # Scenario 3: File does not exist
    if not os.path.exists(args.file):
        print("ERROR: File does not exist.")
        sys.exit(1)
        
    key = derive_key(args.password)
    cipher_suite = Fernet(key)
    
    # Scenario 1 & 2: Decrypt and check password
    try:
        with open(args.file, "rb") as f:
            encrypted_data = f.read()
        plaintext = cipher_suite.decrypt(encrypted_data).decode('utf-8')
    except Exception:
        print("ERROR: Password is incorrect.")
        sys.exit(1)

    # If password is correct, let AI perform semantic search
    # (Importing inside function so 'encrypt' command stays lighting fast)
    from sentence_transformers import SentenceTransformer, util

    chunks = [c.strip() for c in plaintext.split('\n\n') if c.strip()]
    if not chunks:
        print("Vault is empty.")
        return

    model = SentenceTransformer('all-MiniLM-L6-v2')
    query_embedding = model.encode(args.query)
    chunk_embeddings = model.encode(chunks)

    hits = util.semantic_search(query_embedding, chunk_embeddings, top_k=1)
    best_match_idx = hits[0][0]['corpus_id']
    score = hits[0][0]['score']

    # Security check: Only return if it's a reasonably confident semantic match
    if score > 0.3:
        print(f"--- MATCH FOUND ---\n{chunks[best_match_idx]}")
    else:
        print("No highly relevant information found matching those parameters.")

def main():
    parser = argparse.ArgumentParser(description="Secret Agent Courier Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Encrypt Subcommand
    enc_parser = subparsers.add_parser("encrypt")
    enc_parser.add_argument("--password", required=True)
    enc_parser.add_argument("--file", required=True)
    enc_parser.add_argument("--content", required=True)

    # Decrypt Subcommand
    dec_parser = subparsers.add_parser("decrypt")
    dec_parser.add_argument("--password", required=True)
    dec_parser.add_argument("--file", required=True)
    dec_parser.add_argument("--query", required=True)

    args = parser.parse_args()

    if args.command == "encrypt":
        handle_encrypt(args)
    elif args.command == "decrypt":
        handle_decrypt(args)

if __name__ == "__main__":
    main()

# skill-secret: Secret Courier Vault
# v4: Supabase KMS-backed encrypted notes.
# This module is an argparse shim; all behavior lives in flows.py.

import argparse
import sys

import flows


def main():
    p = argparse.ArgumentParser(
        description="Secret Courier Vault (v4: Supabase KMS)",
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

    init_p = sub.add_parser(
        "init", help="initialize a new Supabase KMS database"
    )
    init_p.add_argument("--url", required=True)
    init_p.add_argument("--api-key", required=True)
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

    args = p.parse_args()

    if args.command == "init":
        flows.handle_init(args)
    elif args.command == "take":
        flows.handle_take(args)
    elif args.command == "retrieve":
        flows.handle_retrieve(args)
    elif args.command == "whoami":
        flows.handle_whoami(args)
    else:
        p.print_usage(sys.stderr)
        sys.stderr.write(
            "secret.py: error: a subcommand is required "
            "(init|take|retrieve|whoami)\n"
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
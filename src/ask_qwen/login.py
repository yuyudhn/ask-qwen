"""Login via Qwen auth API (tanpa browser; lihat dev-notes/qwen-api-research.md)."""

import argparse
import getpass

from .auth import AuthError, signin


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign in ke Qwen via API")
    parser.add_argument("--email", help="Qwen account email")
    args = parser.parse_args()
    email = args.email or input("Qwen email: ").strip()
    try:
        auth = signin(email, getpass.getpass("Password: "))
    except AuthError as e:
        raise SystemExit(f"Login gagal: {e}")
    print(f"Login OK: {auth['name']} <{auth['email']}>")

"""CLI: buka browser headed untuk login manual (pertama kali / re-login)."""

import argparse

from .browser import PlaywrightBackend


def main() -> None:
    parser = argparse.ArgumentParser(description="Login manual ke Qwen Web")
    parser.parse_args()
    backend = PlaywrightBackend()
    try:
        ok = backend.ensure_login_headed()
        raise SystemExit(0 if ok else 1)
    finally:
        backend.close()

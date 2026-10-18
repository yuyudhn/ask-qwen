"""CLI interface for ask-qwen — all features as subcommands."""

import argparse
import json
import sys
import threading
import time

from . import daemon


def _get_client():
    daemon.start_background()
    return daemon.DaemonClient()


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _run(func, *args):
    try:
        return func(*args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)


class _Spinner:
    """Animated spinner on stderr while waiting."""
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, message: str = "Thinking") -> None:
        self._message = message
        self._stop = threading.Event()
        self._stopped = False
        self._thread: threading.Thread | None = None

    def __enter__(self):
        if sys.stderr.isatty():
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_):
        if self._stopped:
            return  # idempotent: jalur streaming boleh exit dua kali
        self._stopped = True
        self._stop.set()
        if self._thread:
            self._thread.join()
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = self._FRAMES[i % len(self._FRAMES)]
            elapsed = i * 0.1
            sys.stderr.write(f"\r{frame} {self._message}... {elapsed:.0f}s")
            sys.stderr.flush()
            self._stop.wait(0.1)
            i += 1


def _ask_streaming(client, message: str) -> None:
    """Cetak jawaban per baris saat digenerasi; spinner sampai token pertama.
    Bila tak ada delta (daemon lama/stream kosong), tampilkan jawaban final."""
    spinner = _Spinner()
    spinner.__enter__()
    printed = False
    try:
        stream = client.ask_stream(message)
        while True:
            try:
                delta = next(stream)
            except StopIteration as stop:
                if not printed and stop.value:
                    spinner.__exit__()
                    print(stop.value)
                break
            spinner.__exit__()
            printed = True
            print(delta, end="", flush=True)
        if printed:
            print()
    finally:
        spinner.__exit__()


def cmd_ask(args) -> None:
    message = " ".join(args.message)
    client = _get_client()
    if sys.stdout.isatty():
        # TTY: stream jawaban per baris saat digenerasi; stdout untuk piped
        # tetap satu jawaban bersih di akhir.
        try:
            _ask_streaming(client, message)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            raise SystemExit(1)
    else:
        with _Spinner():
            result = _run(client.ask, message)
        print(result)


def cmd_read(args) -> None:
    result = _run(_get_client().read)
    _print_json(result)


def cmd_new_chat(args) -> None:
    result = _run(_get_client().new_chat)
    print(result)


def cmd_list_chats(args) -> None:
    from . import api

    result = _run(api.list_chats, args.limit)
    _print_json(result)


def cmd_read_chat(args) -> None:
    result = _run(_get_client().read_chat, args.url)
    _print_json(result)


def cmd_open_chat(args) -> None:
    result = _run(_get_client().open_chat, args.url)
    _print_json(result)


def cmd_set_mode(args) -> None:
    result = _run(_get_client().set_mode, args.mode)
    print(result)


def cmd_stop(args) -> None:
    result = _run(_get_client().stop_generation)
    print(result)


def cmd_status(args) -> None:
    result = _run(_get_client().get_status)
    _print_json(result)


def cmd_search(args) -> None:
    result = _run(_get_client().search_chats, args.query)
    _print_json(result)


def cmd_login(args) -> None:
    import getpass

    # Stop daemon first — it holds the browser profile
    if daemon.is_running():
        print("Stopping daemon...", file=sys.stderr)
        daemon.send_quit()
        time.sleep(1)

    from .auth import AuthError, signin

    email = args.email or input("Qwen email: ").strip()
    password = getpass.getpass("Password: ")
    try:
        auth = signin(email, password)
    except AuthError as e:
        print(f"Login gagal: {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Login OK: {auth['name']} <{auth['email']}>")
    print("Browser daemon akan memakai sesi ini otomatis (refresh cookie di-seed).")


def cmd_quit(args) -> None:
    if daemon.is_running():
        daemon.send_quit()
        print("Daemon stopped.")
    else:
        print("Daemon not running.")


def cmd_interactive(args) -> None:
    """Simple REPL: type messages, get answers. Ctrl+D or 'exit' to quit."""
    client = _get_client()
    print("ask-qwen interactive mode (type 'exit' or Ctrl+D to quit)")
    print("Commands: /new (new chat), /mode <auto|thinking|fast>, /read, /status")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() == "exit":
            break
        if line == "/new":
            print(_run(client.new_chat))
            continue
        if line == "/read":
            _print_json(_run(client.read))
            continue
        if line == "/status":
            _print_json(_run(client.get_status))
            continue
        if line.startswith("/mode "):
            print(_run(client.set_mode, line[6:].strip()))
            continue
        try:
            _ask_streaming(client, line)
        except Exception as e:
            print(f"Error: {e}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ask-qwen",
        description="Chat with Qwen Web (chat.qwen.ai) via CLI",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("ask", help="Send a message and get the answer")
    p.add_argument("message", nargs="+", help="Message to send")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("read", help="Read the active chat transcript")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("new", help="Start a new chat (reset conversation)")
    p.set_defaults(func=cmd_new_chat)

    p = sub.add_parser("list", help="List recent chats from sidebar")
    p.add_argument("-n", "--limit", type=int, default=20, help="Max chats to list")
    p.set_defaults(func=cmd_list_chats)

    p = sub.add_parser("read-chat", help="Read a chat transcript by URL or chat ID")
    p.add_argument("url", help="chat.qwen.ai/c/... URL atau chat ID dari `list`")
    p.set_defaults(func=cmd_read_chat)

    p = sub.add_parser("open", help="Open an old chat and make it active")
    p.add_argument("url", help="chat.qwen.ai/c/... URL atau chat ID dari `list`")
    p.set_defaults(func=cmd_open_chat)

    p = sub.add_parser("mode", help="Set Qwen mode: auto, thinking, fast")
    p.add_argument("mode", choices=["auto", "thinking", "fast"])
    p.set_defaults(func=cmd_set_mode)

    p = sub.add_parser("stop", help="Stop active generation")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("status", help="Show login/chat/generation status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="Search chats by text")
    p.add_argument("query", help="Search query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("login", help="Sign in via Qwen API (no browser)")
    p.add_argument("--email", help="Qwen account email (prompted if omitted)")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("chat", help="Interactive chat REPL")
    p.set_defaults(func=cmd_interactive)

    p = sub.add_parser("quit", help="Stop the background browser daemon")
    p.set_defaults(func=cmd_quit)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        raise SystemExit(1)
    args.func(args)

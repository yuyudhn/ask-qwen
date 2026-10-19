"""CLI interface for ask-qwen — pure API, no browser."""

import argparse
import json
import sys
import threading
import time

from . import api, auth, config


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _run(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except (api.ApiError, auth.AuthError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)


class _Spinner:
    """Animated spinner on stderr while waiting."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, message: str = "Thinking") -> None:
        self._message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        if sys.stderr.isatty():
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self._thread:
            self._thread.join()
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = self._FRAMES[i % len(self._FRAMES)]
            sys.stderr.write(f"\r{frame} {self._message}... {i * 0.1:.0f}s")
            sys.stderr.flush()
            self._stop.wait(0.1)
            i += 1


# -- commands -----------------------------------------------------------------


def _resolve_message(parts: list) -> str:
    """Ganti kata '-' dengan konten stdin (pipes).

    `git diff | ask-qwen ask "review this" -` → pesan berisi instruksi +
    diff. Hanya baca stdin bila '-' eksplisit — tidak pernah blokir TTY.
    """
    if "-" not in parts:
        return " ".join(parts)
    if sys.stdin.isatty():
        raise ValueError("arg '-' membutuhkan konten dari pipe/stdin")
    data = sys.stdin.read()
    if not data.strip():
        raise ValueError("stdin kosong")
    return " ".join(data if p == "-" else p for p in parts)


def cmd_ask(args) -> None:
    _ask_impl(args, chat_mode="normal")


def cmd_research(args) -> None:
    # Deep research: jauh lebih lama, fase thinking/riset bisa diam >120s.
    _ask_impl(args, chat_mode="deep_research", idle_timeout_s=600)


def _ask_impl(args, chat_mode: str = "normal", idle_timeout_s: int | None = None) -> None:
    message = _run(_resolve_message, list(args.message))
    # Retry bounded bila server kena high demand (umum di jam sibuk, terutama
    # deep research yang berat). Percobaan gagal tidak menulis node baru, jadi
    # retry aman terhadap rantai percakapan.
    attempts = 3
    for i in range(1, attempts + 1):
        try:
            _ask_once(args, message, chat_mode, idle_timeout_s)
            return
        except (api.ApiError, auth.AuthError, ValueError) as e:
            transient = "high demand" in str(e).lower() or "quota" in str(e).lower()
            if not transient or i == attempts:
                print(f"Error: {e}", file=sys.stderr)
                raise SystemExit(1)
            print(f"Server sibuk ({i}/{attempts - 1}), coba lagi dalam 15s...", file=sys.stderr)
            time.sleep(15)


def _ask_once(args, message: str, chat_mode: str, idle_timeout_s: int | None) -> None:
    if sys.stdout.isatty() and not args.json:
        # TTY: jawaban mengalir langsung ke stdout; --verbose menampilkan
        # fase think/code_interpreter ke stderr. Piped: tetap satu jawaban
        # bersih di akhir agar aman untuk konsumsi agen.
        first = {"answer": True, "phase": None}
        spinner = _Spinner()
        spinner.__enter__()
        stopped = {"v": False}

        def _stop_spinner() -> None:
            if not stopped["v"]:
                stopped["v"] = True
                if first["phase"] is not None:
                    sys.stderr.write("\n")  # jaga teks fase dari clear-line
                spinner.__exit__()

        def _live(chunk: str) -> None:
            _stop_spinner()
            if first["answer"]:
                first["answer"] = False
                print()
            print(chunk, end="", flush=True)

        def _verbose(phase: str, chunk: str) -> None:
            _stop_spinner()
            if first["phase"] != phase:
                first["phase"] = phase
                sys.stderr.write(f"\n[{phase}] ")
            sys.stderr.write(chunk)
            sys.stderr.flush()

        on_phase = _verbose if args.verbose else None
        try:
            result = api.stream_ask(message, on_delta=_live, on_phase=on_phase,
                                    chat_mode=chat_mode, idle_timeout_s=idle_timeout_s)
        except (api.ApiError, auth.AuthError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            raise SystemExit(1)
        finally:
            _stop_spinner()
        if not first["answer"]:  # sudah streaming — cukup newline penutup
            print()
        else:
            print(result["answer"])
    else:
        with _Spinner():
            result = _run(api.stream_ask, message, chat_mode=chat_mode,
                          idle_timeout_s=idle_timeout_s)
        if args.json:
            _print_json(result)
        else:
            print(result["answer"])


def cmd_read(args) -> None:
    chat_id = api.load_state().get("active_chat_id")
    if not chat_id:
        print(
            "Error: tidak ada chat aktif. Gunakan `ask-qwen ask` atau `ask-qwen open` dulu.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    _print_json(_run(api.chat_transcript, chat_id))


def cmd_new_chat(args) -> None:
    # Tandai "chat baru": chat sesungguhnya dibuat API saat ask berikutnya.
    api.set_active_chat(None)
    print("Chat akan direset — pertanyaan berikutnya membuat chat baru.")


def cmd_list_chats(args) -> None:
    _print_json(_run(api.list_chats, args.limit))


def cmd_read_chat(args) -> None:
    chat_id = _run(api.parse_chat_id, args.url)
    _print_json(_run(api.chat_transcript, chat_id))


def cmd_open_chat(args) -> None:
    chat_id = _run(api.parse_chat_id, args.url)
    if not _run(api.chat_exists, chat_id):
        print(f"Error: chat tidak ditemukan: {chat_id}", file=sys.stderr)
        raise SystemExit(1)
    api.set_active_chat(chat_id)
    _print_json(_run(api.chat_transcript, chat_id))


def cmd_set_mode(args) -> None:
    mode = (args.mode or "").strip().lower()
    if mode not in config.MODES:
        print(
            f"Error: mode tidak dikenal: {mode!r}. Pilihan: {', '.join(config.MODES)}.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    api.set_thinking_mode(mode.capitalize())
    print(f"Mode diatur ke {mode.capitalize()}.")


def cmd_model(args) -> None:
    if not args.model_id:
        current = api.get_model()
        for m in _run(api.list_models):
            mark = "*" if m["id"] == current else " "
            print(f"{mark} {m['id']:<24} {m['name']}")
        return
    model_id = args.model_id.strip()
    known = {m["id"] for m in _run(api.list_models)}
    if model_id not in known:
        print(f"Error: model tidak dikenal: {model_id!r}. Lihat: ask-qwen model", file=sys.stderr)
        raise SystemExit(1)
    api.set_model(model_id)
    print(f"Model diatur ke {model_id}.")


def cmd_stop(args) -> None:
    # Stop lintas-proses: tandai di state.json; stream_ask yang memproses.
    api.request_stop()
    print("Stop request diterima.")


def cmd_status(args) -> None:
    state = api.load_state()
    chat_id = state.get("active_chat_id")
    status = {
        "logged_in": auth.session_ok(),
        "generating": False,
        "active_chat": bool(chat_id),
        "chat_id": chat_id,
        "url": f"{config.BASE_URL}/c/{chat_id}" if chat_id else None,
        "thinking_mode": api.get_thinking_mode(),
        "model": api.get_model(),
    }
    if chat_id:
        try:
            status["generating"] = api.is_generating(chat_id)
        except api.ApiError:
            pass
    _print_json(status)


def cmd_search(args) -> None:
    _print_json(_run(api.search_chats, args.query))


def _confirm_delete(targets: list) -> bool:
    if sys.stdin.isatty():
        reply = input(f"Hapus {len(targets)} chat permanen? [y/N] ").strip().lower()
        return reply == "y"
    return False  # non-TTY wajib --yes


def cmd_delete(args) -> None:
    ids = [_run(api.parse_chat_id, ref) for ref in args.refs]
    if not args.yes and not _confirm_delete(ids):
        print("Dibatalkan.")
        return
    if len(ids) == 1:
        _run(api.delete_chat, ids[0])
    else:
        _run(api.delete_chats, ids)
    state = api.load_state()
    if state.get("active_chat_id") in ids:
        api.set_active_chat(None)  # chat aktif terhapus — reset state
    print(f"Terhapus: {len(ids)} chat.")


def cmd_pin(args) -> None:
    chat_id = _run(api.parse_chat_id, args.url)
    _run(api.set_pinned, chat_id, True)
    print(f"Chat dipin: {chat_id}")


def cmd_unpin(args) -> None:
    chat_id = _run(api.parse_chat_id, args.url)
    _run(api.set_pinned, chat_id, False)
    print(f"Chat diunpin: {chat_id}")


def cmd_clone(args) -> None:
    chat_id = _run(api.parse_chat_id, args.url)
    new_id = _run(api.clone_chat, chat_id)
    print(f"Chat dikloning: {config.BASE_URL}/c/{new_id}")


def cmd_share(args) -> None:
    chat_id = _run(api.parse_chat_id, args.url)
    url = _run(api.share_chat, chat_id)
    print(url)


def cmd_project(args) -> None:
    sub = args.project_command
    if sub == "list" or sub is None:
        current = api.get_active_project()
        projects = _run(api.list_projects)
        if not projects:
            print("Tidak ada project. Buat: ask-qwen project new \"Nama\"")
            return
        for p in projects:
            mark = "*" if p["id"] == current else " "
            print(f"{mark} {p['id']}  {p.get('name') or '(unnamed)'}  ({p.get('chat_count') or 0} chats)")
    elif sub == "new":
        pid = _run(api.create_project, args.name)
        print(f"Project dibuat: {pid}")
    elif sub == "open":
        api.set_active_project(args.id)
        print(f"Project aktif: {args.id} — ask/new berikutnya membuat chat di dalamnya.")
    elif sub == "close":
        api.set_active_project(None)
        print("Project aktif dilepas.")
    elif sub == "add":
        pid = args.project or api.get_active_project()
        if not pid:
            print("Error: tidak ada project aktif — pakai --project <id>.", file=sys.stderr)
            raise SystemExit(1)
        chat_id = _run(api.parse_chat_id, args.url)
        _run(api.add_chat_to_project, pid, chat_id)
        print(f"Chat {chat_id} masuk project {pid}.")
    elif sub == "chats":
        _print_json(_run(api.project_chats, args.id))
    elif sub == "delete":
        if not args.yes:
            print("Error: hapus project butuh --yes (aksi destruktif).", file=sys.stderr)
            raise SystemExit(1)
        _run(api.delete_project, args.id)
        if api.get_active_project() == args.id:
            api.set_active_project(None)
        print(f"Project terhapus: {args.id}")


def cmd_login(args) -> None:
    import getpass

    email = args.email or input("Qwen email: ").strip()
    password = getpass.getpass("Password: ")
    try:
        a = auth.signin(email, password)
    except auth.AuthError as e:
        print(f"Login gagal: {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Login OK: {a['name']} <{a['email']}>")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ask-qwen",
        description="Chat with Qwen (chat.qwen.ai) via its REST API — no browser",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("ask", help="Send a message and get the answer")
    p.add_argument("message", nargs="+", help="Message to send; use '-' as a word to insert piped stdin")
    p.add_argument("--json", action="store_true", help="Machine-readable output (answer, chat_id, usage, model)")
    p.add_argument("-v", "--verbose", action="store_true", help="Show thinking/code_interpreter phases on stderr (TTY)")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("research", help="Deep-research a question (much slower, searches the web)")
    p.add_argument("message", nargs="+", help="Research question; '-' inserts piped stdin")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.add_argument("-v", "--verbose", action="store_true", help="Show research/thinking phases on stderr")
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("read", help="Read the active chat transcript")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("new", help="Start a new chat (reset conversation)")
    p.set_defaults(func=cmd_new_chat)

    p = sub.add_parser("list", help="List recent chats")
    p.add_argument("-n", "--limit", type=int, default=20, help="Max chats (default 20)")
    p.set_defaults(func=cmd_list_chats)

    p = sub.add_parser("read-chat", help="Read a chat transcript by URL or chat ID")
    p.add_argument("url", help="chat.qwen.ai/c/... URL or bare chat ID")
    p.set_defaults(func=cmd_read_chat)

    p = sub.add_parser("open", help="Open an old chat and make it active")
    p.add_argument("url", help="chat.qwen.ai/c/... URL or bare chat ID")
    p.set_defaults(func=cmd_open_chat)

    p = sub.add_parser("mode", help="Set thinking mode")
    p.add_argument("mode", choices=config.MODES)
    p.set_defaults(func=cmd_set_mode)

    p = sub.add_parser("model", help="List or set the LLM model")
    p.add_argument("model_id", nargs="?", help="Model id (e.g. qwen3.6-plus); omit to list")
    p.set_defaults(func=cmd_model)

    p = sub.add_parser("stop", help="Stop active generation")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("status", help="Show login/chat/generation status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="Search chats by text")
    p.add_argument("query", help="Search query")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("delete", help="Delete chats permanently (1+ id/URL)")
    p.add_argument("refs", nargs="+", help="Chat id(s) or URL(s)")
    p.add_argument("--yes", action="store_true", help="Skip confirmation (required when piped)")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("pin", help="Pin a chat")
    p.add_argument("url", help="Chat id or URL")
    p.set_defaults(func=cmd_pin)

    p = sub.add_parser("unpin", help="Unpin a chat")
    p.add_argument("url", help="Chat id or URL")
    p.set_defaults(func=cmd_unpin)

    p = sub.add_parser("clone", help="Duplicate a chat")
    p.add_argument("url", help="Chat id or URL")
    p.set_defaults(func=cmd_clone)

    p = sub.add_parser("share", help="Create a public share link for a chat")
    p.add_argument("url", help="Chat id or URL")
    p.set_defaults(func=cmd_share)

    p = sub.add_parser("project", help="Organize chats into projects")
    psub = p.add_subparsers(dest="project_command")
    p.set_defaults(func=cmd_project, project_command=None)
    q = psub.add_parser("list", help="List projects (* = active)")
    q.set_defaults(project_command="list")
    q = psub.add_parser("new", help="Create a project")
    q.add_argument("name", help="Project name")
    q.set_defaults(project_command="new")
    q = psub.add_parser("open", help="Make a project active (future asks create chats inside)")
    q.add_argument("id", help="Project id")
    q.set_defaults(project_command="open")
    q = psub.add_parser("close", help="Detach the active project")
    q.set_defaults(project_command="close")
    q = psub.add_parser("add", help="Move a chat into a project")
    q.add_argument("url", help="Chat id or URL")
    q.add_argument("--project", help="Project id (default: active project)")
    q.set_defaults(project_command="add")
    q = psub.add_parser("chats", help="List chats inside a project")
    q.add_argument("id", help="Project id")
    q.set_defaults(project_command="chats")
    q = psub.add_parser("delete", help="Delete a project (destructive, needs --yes)")
    q.add_argument("id", help="Project id")
    q.add_argument("--yes", action="store_true", help="Confirm deletion")
    q.set_defaults(project_command="delete")

    p = sub.add_parser("login", help="Sign in via Qwen API")
    p.add_argument("--email", help="Qwen account email")
    p.set_defaults(func=cmd_login)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        raise SystemExit(1)
    args.func(args)

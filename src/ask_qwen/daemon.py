"""Background daemon: keeps browser alive, accepts commands via Unix socket."""

import json
import logging
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time

from . import config
from .client import QwenWebClient

log = logging.getLogger("ask_qwen.daemon")

_METHODS = (
    "ask", "read", "new_chat", "list_chats", "read_chat",
    "open_chat", "set_mode", "stop_generation", "get_status",
    "search_chats", "ping",
)


class DaemonServer:
    def __init__(self) -> None:
        self._client = QwenWebClient()
        self._last_activity = time.monotonic()
        self._running = True

    def _dispatch(self, method: str, params: dict):
        if method == "quit":
            self._running = False
            return "bye"
        if method == "ping":
            return "pong"
        if method not in _METHODS:
            raise ValueError(f"unknown method: {method}")
        func = getattr(self._client, method)
        # Build positional/keyword args from params
        import inspect
        sig = inspect.signature(func)
        kwargs = {k: params[k] for k in sig.parameters if k in params}
        return func(**kwargs)

    def _handle(self, conn: socket.socket) -> None:
        try:
            rf = conn.makefile("r")
            wf = conn.makefile("w")
            line = rf.readline()
            if not line:
                return
            request = json.loads(line)
            method = request.get("method", "")
            params = request.get("params", {})
            try:
                result = self._dispatch(method, params)
                wf.write(json.dumps({"ok": True, "result": result}, ensure_ascii=False) + "\n")
            except Exception as e:
                wf.write(json.dumps({"ok": False, "error": str(e) or type(e).__name__}) + "\n")
            wf.flush()
        except Exception:
            log.exception("request handler error")
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._last_activity = time.monotonic()

    def _warmup(self) -> None:
        """Pre-launch browser so first real request is fast."""
        try:
            self._client.get_status()
        except Exception:
            log.debug("warmup failed (will retry on first request)", exc_info=True)

    def run(self) -> None:
        sock_path = str(config.DAEMON_SOCKET)
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Clean stale socket
        if os.path.exists(sock_path):
            os.unlink(sock_path)

        # Write PID
        config.DAEMON_PID_FILE.write_text(str(os.getpid()))

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(sock_path)
            sock.listen(5)
            sock.setblocking(False)
            log.info("daemon listening on %s (pid %d)", sock_path, os.getpid())

            # Warm up browser in background
            threading.Thread(target=self._warmup, daemon=True).start()

            while self._running:
                ready, _, _ = select.select([sock], [], [], 5.0)
                if ready:
                    conn, _ = sock.accept()
                    # Handle sequentially — browser ops are serialized anyway
                    self._handle(conn)
                elif time.monotonic() - self._last_activity > config.DAEMON_IDLE_TIMEOUT_S:
                    log.info("idle timeout (%ds), shutting down", config.DAEMON_IDLE_TIMEOUT_S)
                    break
        finally:
            self._client.close()
            sock.close()
            for path in (sock_path, str(config.DAEMON_PID_FILE)):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            log.info("daemon stopped")


def is_running() -> bool:
    """Check if daemon is reachable."""
    sock_path = str(config.DAEMON_SOCKET)
    if not os.path.exists(sock_path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        # Stale socket
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        return False


def start_background() -> None:
    """Start daemon as a detached background process."""
    if is_running():
        return
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(str(config.LOG_FILE), "a")
    subprocess.Popen(
        [sys.executable, "-m", "ask_qwen.daemon"],
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
        cwd="/",
    )
    # Wait for socket to appear
    for _ in range(100):  # 10 seconds
        time.sleep(0.1)
        if is_running():
            return
    raise RuntimeError("daemon failed to start — check " + str(config.LOG_FILE))


def send_quit() -> None:
    """Ask the daemon to shut down."""
    sock_path = str(config.DAEMON_SOCKET)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        wf = s.makefile("w")
        wf.write(json.dumps({"method": "quit"}) + "\n")
        wf.flush()
        s.close()
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        pass
    # Wait for socket to disappear
    for _ in range(50):
        time.sleep(0.1)
        if not os.path.exists(sock_path):
            return


class DaemonClient:
    """Proxy: same interface as QwenWebClient, forwards to daemon via socket."""

    def _request(self, method: str, **params):
        sock_path = str(config.DAEMON_SOCKET)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(sock_path)
            wf = s.makefile("w")
            rf = s.makefile("r")
            wf.write(json.dumps({"method": method, "params": params}) + "\n")
            wf.flush()
            # No timeout — browser ops can take minutes
            line = rf.readline()
            if not line:
                raise ConnectionError("daemon closed connection unexpectedly")
            resp = json.loads(line)
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "unknown daemon error"))
            return resp["result"]
        finally:
            s.close()

    def ask(self, message: str) -> str:
        return self._request("ask", message=message)

    def read(self) -> list[dict]:
        return self._request("read")

    def new_chat(self) -> str:
        return self._request("new_chat")

    def list_chats(self, limit: int = 20) -> list[dict]:
        return self._request("list_chats", limit=limit)

    def read_chat(self, url: str) -> list[dict]:
        return self._request("read_chat", url=url)

    def open_chat(self, url: str) -> list[dict]:
        return self._request("open_chat", url=url)

    def set_mode(self, mode: str) -> str:
        return self._request("set_mode", mode=mode)

    def stop_generation(self) -> str:
        return self._request("stop_generation")

    def get_status(self) -> dict:
        return self._request("get_status")

    def search_chats(self, query: str) -> list[dict]:
        return self._request("search_chats", query=query)

    def close(self) -> None:
        pass  # daemon stays alive


def main() -> None:
    """Entry point: python -m ask_qwen.daemon"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = DaemonServer()

    def _signal(signum, frame):
        server._running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _signal)

    server.run()


if __name__ == "__main__":
    main()

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
from .browser import AuthExpired, ProfileLocked, WAFChallenge
from .client import QwenWebClient

log = logging.getLogger("ask_qwen.daemon")

_METHODS = (
    "ask", "new_chat", "set_mode", "set_model", "stop_generation", "ping",
)

# Tipe error yang dilewatkan utuh lewat socket agar CLI bisa memetakan
# langkah pemulihan yang benar (login vs tunggu vs tutup browser).
_ERROR_TYPES = {
    "AuthExpired": AuthExpired,
    "WAFChallenge": WAFChallenge,
    "ProfileLocked": ProfileLocked,
}


class DaemonServer:
    def __init__(self) -> None:
        self._client = QwenWebClient()
        self._last_activity = time.monotonic()
        self._running = True

    def _dispatch(self, method: str, params: dict, on_delta=None):
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
        if method == "ask" and on_delta is not None:
            return func(on_delta=on_delta, **kwargs)
        return func(**kwargs)

    def _handle(self, conn: socket.socket) -> None:
        # Update aktivitas di AWAL request: request yang lama (ask berjarak
        # menit) tidak boleh memicu idle-shutdown di tengah jalan.
        self._last_activity = time.monotonic()
        rf = wf = None
        try:
            rf = conn.makefile("r")
            wf = conn.makefile("w")
            line = rf.readline()
            if not line:
                return
            request = json.loads(line)
            method = request.get("method", "")
            params = request.get("params", {})
            stream = bool(request.get("stream")) and method == "ask"

            def on_delta(text):
                self._last_activity = time.monotonic()
                try:
                    wf.write(
                        json.dumps({"event": "delta", "text": text}, ensure_ascii=False)
                        + "\n"
                    )
                    wf.flush()
                except OSError:
                    pass  # client pergi; lanjut sampai jawaban selesai

            try:
                result = self._dispatch(method, params, on_delta if stream else None)
                wf.write(json.dumps({"ok": True, "result": result}, ensure_ascii=False) + "\n")
            except Exception as e:
                wf.write(
                    json.dumps(
                        {
                            "ok": False,
                            "error": str(e) or type(e).__name__,
                            "type": type(e).__name__,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            wf.flush()
        except Exception:
            log.exception("request handler error")
        finally:
            for f in (rf, wf):
                try:
                    if f:
                        f.close()
                except OSError:
                    pass
            try:
                conn.close()
            except OSError:
                pass

    def _warmup(self) -> None:
        """Pre-launch browser so first real request is fast."""
        try:
            self._client.warmup()
        except Exception:
            log.debug("warmup failed (will retry on first request)", exc_info=True)

    def run(self) -> None:
        sock_path = str(config.DAEMON_SOCKET)
        config.STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Clean stale socket
        if os.path.exists(sock_path):
            os.unlink(sock_path)

        # Write PID
        config.DAEMON_PID_FILE.write_text(str(os.getpid()))

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(sock_path)
            os.chmod(sock_path, 0o600)  # hanya pemilik yang boleh mengendalikan daemon
            sock.listen(5)
            sock.setblocking(False)
            log.info("daemon listening on %s (pid %d)", sock_path, os.getpid())

            # Warm up browser in background
            threading.Thread(target=self._warmup, daemon=True).start()

            while self._running:
                ready, _, _ = select.select([sock], [], [], 5.0)
                if ready:
                    conn, _ = sock.accept()
                    # Thread per connection: RPC non-browser (stop/status) harus
                    # bisa masuk saat ask streaming — khususnya request_stop yang
                    # hanya men-set flag lintas-thread. Operasi browser tetap
                    # ter-serialisasi oleh executor single-thread di QwenWebClient.
                    threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
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

    def _responses(self, method: str, params: dict, stream: bool = False):
        """Yield response lines dari daemon hingga baris final (ok/error).

        Generator harus dihabiskan atau ditutup agar socket tertutup rapi.
        """
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(config.DAEMON_REQUEST_TIMEOUT_S)
        try:
            s.connect(str(config.DAEMON_SOCKET))
            wf = s.makefile("w")
            rf = s.makefile("r")
            wf.write(
                json.dumps({"method": method, "params": params, "stream": stream})
                + "\n"
            )
            wf.flush()
            while True:
                line = rf.readline()
                if not line:
                    raise ConnectionError("daemon closed connection unexpectedly")
                yield json.loads(line)
        except socket.timeout:
            raise RuntimeError(
                f"Daemon tidak merespons dalam {config.DAEMON_REQUEST_TIMEOUT_S}s "
                "(kemungkinan hang). Jalankan `ask-qwen quit` lalu ulangi."
            ) from None
        finally:
            s.close()

    @staticmethod
    def _error_from(resp: dict) -> Exception:
        exc_type = _ERROR_TYPES.get(resp.get("type", ""), RuntimeError)
        return exc_type(resp.get("error", "unknown daemon error"))

    def _request(self, method: str, **params):
        for resp in self._responses(method, params):
            if resp.get("event") == "delta":
                continue  # streaming ask: abaikan delta, ambil baris final
            if not resp.get("ok"):
                raise self._error_from(resp)
            return resp["result"]

    def ask_stream(self, message: str):
        """Yield delta jawaban saat digenerasi (untuk TTY streaming).

        Bila selesai tanpa delta (daemon lama / tak ada streaming), nilai
        return generator berisi jawaban final utuh.
        """
        for resp in self._responses("ask", {"message": message}, stream=True):
            if resp.get("event") == "delta":
                yield resp["text"]
            elif not resp.get("ok"):
                raise self._error_from(resp)
            else:
                return resp["result"]

    def ask(self, message: str) -> str:
        return self._request("ask", message=message)

    def new_chat(self) -> str:
        return self._request("new_chat")

    def set_mode(self, mode: str) -> str:
        return self._request("set_mode", mode=mode)

    def set_model(self, model_id: str) -> str:
        return self._request("set_model", model_id=model_id)

    def stop_generation(self) -> str:
        return self._request("stop_generation")

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

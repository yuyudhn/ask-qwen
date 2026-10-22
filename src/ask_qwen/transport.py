"""HTTP transport: curl primary, urllib fallback.

Qwen's WAF fast-lanes curl's TLS/HTTP fingerprint; Python's OpenSSL stack
diketahui di-stall (puluh detik) padahal request identik via curl instant.
SSE di-stream via curl -N; idle stream diputus dengan --speed-time/limit.
"""

import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

HAS_CURL = shutil.which("curl") is not None

# batas bawah agar header takut disalahartikan sebagai opsi curl
_MAX_HEADER = 200


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _curl_cmd(method: str, url: str, headers: dict, timeout: int) -> list:
    cmd = ["curl", "-sS", "-f", "-X", method, "--max-time", str(timeout), "-T", "-", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    return cmd


def request(method: str, url: str, headers: dict, data: bytes | None = None,
            timeout: int = 60) -> tuple[int, bytes]:
    """Satu request non-stream; return (status, body). Raise HttpError."""
    body = data or b""
    if HAS_CURL:
        cmd = _curl_cmd(method, url, headers, timeout)
        with tempfile.NamedTemporaryFile() as out:
            cmd += ["-o", out.name, "-w", "%{http_code}"]
            proc = subprocess.run(cmd, input=body, capture_output=True, timeout=timeout + 10)
            code = (proc.stdout or b"").decode().strip() or "000"
            status = int(code) if code.isdigit() else 0
            payload = open(out.name, "rb").read()
        if proc.returncode != 0:
            raise HttpError(
                f"curl exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:200]}",
                status or None,
            )
        return status, payload
    # fallback urllib
    req = urllib.request.Request(url, data=body or None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(f"HTTP {e.code}: {e.read()[:200]!r}", e.code) from e
    except urllib.error.URLError as e:
        raise HttpError(f"koneksi gagal: {e.reason}") from e


def stream(method: str, url: str, headers: dict, data: bytes | None,
           idle_timeout_s: int, total_timeout_s: int = 900):
    """SSE stream: generator yielding str lines (tanpa \\n).

    Raise HttpError bila koneksi/HTTP gagal; generator selesai saat server
    menutup stream. Idle: curl abort bila <1 B/s selama idle_timeout_s.
    """
    if HAS_CURL:
        cmd = ["curl", "-sS", "-f", "-N", "--no-buffer", "-X", method, "-T", "-",
               "--max-time", str(total_timeout_s),
               "--speed-time", str(idle_timeout_s), "--speed-limit", "1", url]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        try:
            proc.stdin.write(data or b"")
            proc.stdin.close()
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                yield raw.decode("utf-8", "replace").rstrip("\r\n")
            rc = proc.wait()
            if rc != 0:
                err = (proc.stderr or b"").read().decode("utf-8", "replace")[:200]
                raise HttpError(f"curl exit {rc}: {err}")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        return
    # fallback urllib (blocking, tanpa idle detection halus)
    import http.client
    import socket

    host_path = url.split("://", 1)[1]
    host, _, path = host_path.partition("/")
    conn = http.client.HTTPSConnection(host, 443, timeout=idle_timeout_s)
    try:
        conn.request(method, "/" + path, body=data, headers=headers)
        resp = conn.getresponse()
        if resp.status != 200:
            raise HttpError(f"HTTP {resp.status}", resp.status)
        while True:
            try:
                line = resp.readline()
            except (socket.timeout, TimeoutError):
                break
            if not line:
                break
            yield line.decode("utf-8", "replace").rstrip("\r\n")
    finally:
        conn.close()

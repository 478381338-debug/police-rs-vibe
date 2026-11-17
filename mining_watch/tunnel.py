"""本地 HTTPS 隧道。

背景：本机 GDAL 的 TLS 栈（curl + Schannel）对任意 https 站点都握手失败
（错误信息是 GBK 编码的 Windows 网络错误，rasterio 解码时会先抛
UnicodeDecodeError，把真实原因掩盖掉）。而 Python 的 ssl/OpenSSL 正常。

做法：在本机起一个 HTTP 正向代理，GDAL 用 http:// 方式请求，代理替它走
https:// 上游（由 Python 完成 TLS 校验）。数据链路依然是加密的，只是
GDAL 到本机回环这一段是明文——回环不出网卡，不降低机密性。

工程要点：
  - 上游按主机维护空闲连接（超过 IDLE_TTL 秒即弃用），避免每次 Range 都握手；
  - 复用到的连接可能已被对端关闭，故单次请求失败即换新连接重试一次；
  - 响应头一旦发出，后续任何异常都只关连接、不再写错误页，否则 curl 会把
    错误页当成正文，导致 TIFFFillTile 读到 0 字节。
"""
from __future__ import annotations

import http.client
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_FORWARD_HEADERS = (
    "Range",
    "If-None-Match",
    "If-Modified-Since",
    "If-Range",
    "Accept",
    "User-Agent",
)
_RETURN_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "ETag",
    "Last-Modified",
)

IDLE_TTL = 20.0
STATS = {"requests": 0, "bytes": 0, "reused": 0, "retried": 0}

_IDLE: dict[str, list[tuple[http.client.HTTPSConnection, float]]] = {}
_IDLE_LOCK = threading.Lock()


def _checkout(host: str) -> tuple[http.client.HTTPSConnection, bool]:
    with _IDLE_LOCK:
        bucket = _IDLE.get(host)
        while bucket:
            conn, ts = bucket.pop()
            if time.time() - ts < IDLE_TTL:
                STATS["reused"] += 1
                return conn, True
            conn.close()
    return http.client.HTTPSConnection(host, timeout=60), False


def _checkin(host: str, conn: http.client.HTTPSConnection) -> None:
    with _IDLE_LOCK:
        bucket = _IDLE.setdefault(host, [])
        if len(bucket) < 8:
            bucket.append((conn, time.time()))
        else:
            conn.close()


def _send(
    host: str, method: str, target: str, fwd: list[tuple[str, str]]
) -> tuple[http.client.HTTPSConnection, http.client.HTTPResponse]:
    """发起请求；复用连接失败时换新连接重试一次。"""
    last: Exception | None = None
    for attempt in range(2):
        conn, _ = _checkout(host)
        try:
            conn.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host)
            for name, value in fwd:
                conn.putheader(name, value)
            conn.endheaders()
            return conn, conn.getresponse()
        except Exception as exc:
            last = exc
            conn.close()
            if attempt == 0:
                STATS["retried"] += 1
    raise last if last else RuntimeError("upstream unavailable")


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 静默
        pass

    def _forward(self, method: str) -> None:
        url = self.path
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        elif not url.startswith("https://"):
            self.send_error(400, "absolute URI required")
            return

        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        fwd = [(n, self.headers[n]) for n in _FORWARD_HEADERS if self.headers.get(n)]

        try:
            conn, resp = _send(host, method, target, fwd)
        except Exception as exc:
            self.send_error(502, f"upstream {type(exc).__name__}"[:120])
            return

        STATS["requests"] += 1
        complete = False

        try:
            self.send_response(resp.status)
            for name in _RETURN_HEADERS:
                value = resp.getheader(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()

            if method == "HEAD":
                resp.read()
                complete = True
            else:
                while True:
                    chunk = resp.read(262144)
                    if not chunk:
                        complete = True
                        break
                    STATS["bytes"] += len(chunk)
                    self.wfile.write(chunk)
        except Exception:
            # 响应头已发出，只能截断，让 curl/GDAL 自己重试
            self.close_connection = True
        finally:
            if complete and not resp.will_close:
                _checkin(host, conn)
            else:
                conn.close()

    def do_GET(self) -> None:
        self._forward("GET")

    def do_HEAD(self) -> None:
        self._forward("HEAD")


class HttpsTunnel:
    def __init__(self) -> None:
        self._srv: ThreadingHTTPServer | None = None
        self.port: int | None = None

    def start(self) -> int:
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self.port

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

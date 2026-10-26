"""A dependency-free local HTTP server for the ModScope web interface."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import traceback
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .analyzer import Analyzer, ScanError, discover_path_files, validate_options
from .models import FileRef, ScanOptions


MAX_JSON_BODY = 1 * 1024 * 1024
MAX_UPLOAD_BODY = 2 * 1024 * 1024 * 1024
SESSION_TTL = timedelta(hours=3)
MAX_SESSIONS = 12
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
    "/favicon.svg": "favicon.svg",
    "/favicon.ico": None,
}
STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@dataclass
class UploadEntry:
    stored_path: str
    name: str
    size: int
    disabled: bool = False


@dataclass
class UploadSession:
    session_id: str
    directory: str
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    entries: list[UploadEntry] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def as_refs(self, include_disabled: bool) -> list[FileRef]:
        with self.lock:
            entries = list(self.entries)
        refs = []
        for entry in entries:
            if entry.disabled and not include_disabled:
                continue
            refs.append(FileRef(
                path=entry.stored_path,
                display_name=entry.name,
                disabled=entry.disabled,
                relative_name=entry.name,
            ))
        return refs


class AppContext:
    def __init__(self, web_root: Path):
        self.web_root = web_root
        self.sessions: OrderedDict[str, UploadSession] = OrderedDict()
        self.sessions_lock = threading.Lock()
        self.scan_gate = threading.BoundedSemaphore(2)

    def new_session(self) -> UploadSession:
        self._cleanup_expired()
        # ``tempfile.mkdtemp`` creates a private directory that some Windows
        # sandbox ACL implementations make unwritable for later file opens.
        # Creating a normal directory with a cryptographically random name is
        # equally private and behaves consistently across environments.
        root = Path(tempfile.gettempdir()) / "modscope-uploads"
        root.mkdir(parents=True, exist_ok=True)
        session_id = os.urandom(18).hex()
        directory = root / session_id
        directory.mkdir(parents=False, exist_ok=False)
        session = UploadSession(session_id, str(directory))
        with self.sessions_lock:
            while len(self.sessions) >= MAX_SESSIONS:
                _, old = self.sessions.popitem(last=False)
                _remove_tree(old.directory)
            self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> UploadSession | None:
        with self.sessions_lock:
            return self.sessions.get(session_id)

    def drop_session(self, session_id: str) -> bool:
        with self.sessions_lock:
            session = self.sessions.pop(session_id, None)
        if session:
            _remove_tree(session.directory)
            return True
        return False

    def close(self) -> None:
        with self.sessions_lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            _remove_tree(session.directory)

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - SESSION_TTL.total_seconds()
        with self.sessions_lock:
            expired = [
                sid for sid, session in self.sessions.items()
                if session.updated < cutoff
            ]
            for sid in expired:
                session = self.sessions.pop(sid)
                _remove_tree(session.directory)


def _remove_tree(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _safe_display_name(raw: str) -> str:
    text = unquote(raw or "").replace("\\", "/").strip()
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    parts = []
    for part in text.split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        part = part.replace("\x00", "")
        parts.append(part[:160])
    result = "/".join(parts)
    if not result:
        result = "uploaded.jar"
    return result[-280:]


def _is_disabled(name: str) -> bool:
    return name.lower().endswith(".disabled")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    for key, value in SECURITY_HEADERS.items():
        handler.send_header(key, value)
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _error(handler: BaseHTTPRequestHandler, status: int, message: str,
           detail: str | None = None) -> None:
    payload: dict[str, Any] = {"ok": False, "error": message}
    if detail:
        payload["detail"] = detail
    _json_response(handler, status, payload)


def _request_origin_ok(handler: BaseHTTPRequestHandler) -> bool:
    origin = handler.headers.get("Origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
    fetch_site = handler.headers.get("Sec-Fetch-Site")
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return False
    return True


class ModScopeHandler(BaseHTTPRequestHandler):
    context: AppContext
    server_version = "ModScope/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the console quiet except for actual failures.
        return

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _check_mutation(self) -> bool:
        if self.headers.get("X-ModScope-Request") != "1":
            _error(self, 403, "请求被拒绝：缺少本地应用标头。")
            return False
        if not _request_origin_ok(self):
            _error(self, 403, "请求被拒绝：来源不是本机应用。")
            return False
        return True

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            _error(self, 400, "Content-Length 无效。")
            return None
        if length < 0 or length > MAX_JSON_BODY:
            _error(self, 413, "请求体过大。")
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _error(self, 400, "请求 JSON 格式无效。")
            return None
        if not isinstance(data, dict):
            _error(self, 400, "请求内容必须是 JSON 对象。")
            return None
        return data

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            _json_response(self, 200, {
                "ok": True,
                "name": "ModScope",
                "version": "1.0.0",
                "offline": True,
            })
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/api/"):
            _error(self, 404, "接口不存在。")
            return
        self._serve_static(path)

    def _serve_static(self, path: str) -> None:
        relative = STATIC_FILES.get(path)
        if relative is None:
            _error(self, 404, "页面不存在。")
            return
        file_path = (self.context.web_root / relative).resolve()
        try:
            file_path.relative_to(self.context.web_root.resolve())
        except ValueError:
            _error(self, 404, "页面不存在。")
            return
        if not file_path.is_file():
            _error(self, 404, "页面文件缺失，请重新下载应用。")
            return
        try:
            data = file_path.read_bytes()
        except OSError:
            _error(self, 500, "读取页面文件失败。")
            return
        content_type = STATIC_MIME.get(file_path.suffix.lower())
        if content_type is None:
            content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript", "application/json", "image/svg+xml"
        }:
            if "charset" not in content_type:
                content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------------
    # POST / PUT / DELETE
    # ------------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._check_mutation():
            return
        try:
            if path == "/api/uploads":
                body = self._read_json()
                if body is None:
                    return
                session = self.context.new_session()
                _json_response(self, 200, {
                    "ok": True,
                    "session_id": session.session_id,
                })
                return
            if path.startswith("/api/upload/"):
                session_id = path.rsplit("/", 1)[-1]
                self._upload_file(session_id, parsed.query)
                return
            if path == "/api/scan":
                body = self._read_json()
                if body is None:
                    return
                self._scan(body)
                return
            if path == "/api/pick-folder":
                body = self._read_json()
                if body is None:
                    return
                try:
                    from .folder_picker import choose_folder
                    folder = choose_folder()
                except Exception as exc:
                    _error(self, 500, "无法打开系统文件夹选择器。", str(exc))
                    return
                _json_response(self, 200, {"ok": True, "path": folder or ""})
                return
            _error(self, 404, "接口不存在。")
        except ScanError as exc:
            _error(self, 400, str(exc))
        except Exception as exc:
            traceback.print_exc()
            _error(self, 500, "扫描时发生内部错误。", str(exc))

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._check_mutation():
            return
        path = urlparse(self.path).path
        if path.startswith("/api/uploads/"):
            session_id = path.rsplit("/", 1)[-1]
            dropped = self.context.drop_session(session_id)
            _json_response(self, 200, {"ok": True, "removed": dropped})
            return
        _error(self, 404, "接口不存在。")

    def do_OPTIONS(self) -> None:  # noqa: N802
        _error(self, 405, "不支持跨域请求。")

    # ------------------------------------------------------------------
    # API implementation
    # ------------------------------------------------------------------
    def _upload_file(self, session_id: str, query: str) -> None:
        session = self.context.get_session(session_id)
        if not session:
            _error(self, 404, "上传会话不存在或已过期。")
            return
        params = parse_qs(query, keep_blank_values=True)
        name = _safe_display_name((params.get("name") or ["uploaded.jar"])[0])
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            _error(self, 400, "Content-Length 无效。")
            return
        if length <= 0:
            _error(self, 400, "上传文件为空。")
            return
        if length > MAX_UPLOAD_BODY:
            _error(self, 413, "单个 MOD 文件不能超过 2 GiB。")
            return
        with session.lock:
            slot = len(session.entries) + 1
            stored_name = f"{slot:05d}.jar"
            stored_path = os.path.join(session.directory, stored_name)
        remaining = length
        try:
            with open(stored_path, "wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError("上传连接提前关闭")
                    handle.write(chunk)
                    remaining -= len(chunk)
        except Exception as exc:
            try:
                os.remove(stored_path)
            except OSError:
                pass
            _error(self, 400, "写入上传文件失败。", str(exc))
            return
        entry = UploadEntry(stored_path, name, length, _is_disabled(name))
        with session.lock:
            session.entries.append(entry)
            session.updated = time.time()
        _json_response(self, 200, {
            "ok": True,
            "name": name,
            "size": length,
            "disabled": entry.disabled,
        })

    def _scan(self, body: dict[str, Any]) -> None:
        options = ScanOptions(
            mc_version=str(body.get("mc_version") or ""),
            loader=str(body.get("loader") or "auto"),
            side=str(body.get("side") or "client"),
            java_version=body.get("java_version"),
            deep_scan=bool(body.get("deep_scan", True)),
            include_disabled=bool(body.get("include_disabled", True)),
            source_kind="upload" if body.get("upload_session") else "path",
        )
        options = validate_options(options)
        upload_session_id = str(body.get("upload_session") or "").strip()
        if upload_session_id:
            session = self.context.get_session(upload_session_id)
            if not session:
                raise ScanError("上传会话不存在或已过期，请重新选择 MOD 文件。")
            refs = session.as_refs(options.include_disabled)
            if not refs:
                raise ScanError("没有可分析的 MOD 文件。")
            source_label = f"本地上传 · {len(refs)} 个文件"
        else:
            refs, source_label = discover_path_files(str(body.get("path") or ""), options)
        if not self.context.scan_gate.acquire(timeout=120):
            raise ScanError("扫描任务过多，请稍后再试。")
        try:
            report = Analyzer(options).analyze(refs, source_label)
        finally:
            self.context.scan_gate.release()
        _json_response(self, 200, {"ok": True, "report": report})


def create_server(host: str = "127.0.0.1", port: int = 8765,
                  web_root: Path | None = None) -> tuple[ThreadingHTTPServer, AppContext, int]:
    if web_root is None:
        web_root = Path(__file__).resolve().parent / "web"
    context = AppContext(web_root)
    handler_class = type("BoundModScopeHandler", (ModScopeHandler,), {"context": context})
    server = ThreadingHTTPServer((host, port), handler_class)
    server.daemon_threads = True
    actual_port = server.server_address[1]
    return server, context, actual_port


def run_server(host: str = "127.0.0.1", port: int = 8765,
               open_browser: bool = True) -> None:
    server, context, actual_port = create_server(host, port)
    url = f"http://127.0.0.1:{actual_port}"
    banner = f"""
  ╭────────────────────────────────────────────╮
  │  ModScope · 我的世界 MOD 冲突查找器        │
  │  本地运行 · 文件不会上传到互联网           │
  ╰────────────────────────────────────────────╯

  打开地址：{url}
  停止服务：按 Ctrl+C，或直接关闭此窗口
"""
    print(banner, flush=True)
    if open_browser:
        import webbrowser
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n正在停止 ModScope…", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        context.close()


__all__ = ["create_server", "run_server", "AppContext"]

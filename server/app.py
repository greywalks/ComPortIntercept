from __future__ import annotations

import base64
import ctypes
import hmac
import json
import os
import secrets
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

try:
    from .archive import Archive, StreamAssembler, now
except ImportError:  # python server/app.py
    from archive import Archive, StreamAssembler, now

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
PRINTERS = {int(p["target_id"]): p for p in CONFIG["printers"]}
CAPTURE_DEVICE = CONFIG.get("capture_device", r"\\.\ZebraUsbTap")
IOCTL_PEEK, IOCTL_ACK, IOCTL_STATS = 0x226008, 0x22600C, 0x226010
HEADER = struct.Struct("<IIIIQQQ")
STATS = struct.Struct("<IIQQQQ")
MAX_CHUNK = 1024 * 1024
MAX_HTTP_BODY = 2 * 1024 * 1024


def secure_token(data_dir):
    """Create a per-install secret, restricted to Administrators/SYSTEM on Windows."""
    private = Path(data_dir) / "private"
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        # Reset explicit/inherited entries before applying a protected DACL.
        # Run elevated and keep the whole installation in an admin-only folder.
        subprocess.run(["icacls.exe", str(private), "/reset"], check=True, capture_output=True)
        subprocess.run(["icacls.exe", str(private), "/inheritance:r", "/grant:r",
                        "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
                       check=True, capture_output=True)
    else:
        os.chmod(private, 0o700)
    token_file = private / "api-token"
    try:
        fd = os.open(token_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(secrets.token_urlsafe(32))
            f.flush()
            os.fsync(f.fileno())
    if os.name == "nt":
        subprocess.run(["icacls.exe", str(token_file), "/reset"], check=True, capture_output=True)
        subprocess.run(["icacls.exe", str(token_file), "/inheritance:r", "/grant:r",
                        "*S-1-5-18:F", "*S-1-5-32-544:F"], check=True, capture_output=True)
    else:
        os.chmod(token_file, 0o600)
    token = token_file.read_text(encoding="ascii").strip()
    if len(token) < 32 or not token.isascii():
        raise RuntimeError("Invalid archive API token")
    return token


class CaptureService:
    def __init__(self, archive, config):
        self.archive, self.config = archive, config
        self.state = {"connected": False, "last_error": None, "last_capture_at": None,
                      "driver_session": None, "queued_bytes": 0, "dropped_chunks": 0, "dropped_bytes": 0}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self):
        self.thread = threading.Thread(target=self.run, name="capture", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def run(self):
        if os.name != "nt" or not self.config.get("capture_enabled", True):
            self.update(connected=False, last_error="Capture disabled or unavailable on this operating system")
            return
        while not self.stop_event.is_set():
            try:
                assembler = StreamAssembler(self.archive, int(self.config.get("standalone_flush_ms", 400)),
                                            int(self.config.get("max_segment_bytes", 4 * 1024 * 1024)))
                assembler.recover()
                self._poll_windows(assembler)
            except Exception as exc:
                # A dead or blocked capture worker must never remain green.
                self.update(connected=False, last_error=f"Capture stopped: {exc}")
            self.stop_event.wait(2)

    def _poll_windows(self, assembler):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create, control, close = k32.CreateFileW, k32.DeviceIoControl, k32.CloseHandle
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        control.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        control.restype = wintypes.BOOL
        close.argtypes = [wintypes.HANDLE]
        handle = create(self.config.get("capture_device", CAPTURE_DEVICE), 0x80000000, 0x3, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "Unable to open capture device")
        try:
            buffer = ctypes.create_string_buffer(MAX_CHUNK + HEADER.size)
            statsbuf = ctypes.create_string_buffer(STATS.size)
            next_stats = 0.0
            while not self.stop_event.is_set():
                if time.monotonic() >= next_stats:
                    got = wintypes.DWORD()
                    if not control(handle, IOCTL_STATS, None, 0, statsbuf, len(statsbuf), ctypes.byref(got), None):
                        code = ctypes.get_last_error()
                        raise RuntimeError(f"Driver protocol v2 unavailable (Windows error {code}); update driver and service together")
                    if got.value != STATS.size:
                        raise RuntimeError("Invalid driver statistics response")
                    version, _, session, queued, dropped, dropped_bytes = STATS.unpack(statsbuf.raw)
                    if version != 2:
                        raise RuntimeError(f"Unsupported driver protocol {version}")
                    self.archive.observe_session(session, queued)
                    if dropped:
                        self.archive.record_loss(session, dropped, dropped_bytes)
                    self.update(connected=True, last_error=None, driver_session=str(session), queued_bytes=queued,
                                dropped_chunks=dropped, dropped_bytes=dropped_bytes)
                    next_stats = time.monotonic() + 0.5
                got = wintypes.DWORD()
                if not control(handle, IOCTL_PEEK, None, 0, buffer, len(buffer), ctypes.byref(got), None):
                    code = ctypes.get_last_error()
                    if code == 259:  # ERROR_NO_MORE_ITEMS
                        assembler.flush_idle()
                        self.stop_event.wait(0.05)
                        continue
                    raise OSError(code, "Capture peek failed")
                if got.value < HEADER.size:
                    raise RuntimeError("Short capture header")
                version, target, length, _, seq, session, stamp = HEADER.unpack_from(buffer.raw)
                if version != 2 or length > MAX_CHUNK or got.value != HEADER.size + length:
                    raise RuntimeError("Invalid capture response")
                raw = bytes(buffer.raw[HEADER.size:HEADER.size + length])
                inserted = self.archive.put_chunk(session, seq, target, stamp, raw)
                ack_data = ctypes.create_string_buffer(struct.pack("<QQ", session, seq))
                ack_got = wintypes.DWORD()
                if not control(handle, IOCTL_ACK, ack_data, 16, None, 0, ctypes.byref(ack_got), None):
                    raise OSError(ctypes.get_last_error(), "Capture acknowledgement failed")
                if inserted:
                    assembler.feed(session, seq, target, raw, stamp)
                self.update(last_capture_at=now())
                assembler.flush_idle()
            assembler.flush_idle(force=True, reason="service-stop")
        finally:
            close(handle)
            self.update(connected=False)


def raw_print(queue, raw, title):
    if os.name != "nt":
        raise RuntimeError("Reprint requires Windows")
    class DOC_INFO_1W(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR), ("pDatatype", wintypes.LPWSTR)]
    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
    ws.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    ws.OpenPrinterW.restype = wintypes.BOOL
    ws.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
    ws.StartDocPrinterW.restype = wintypes.DWORD
    for name in ("StartPagePrinter", "EndPagePrinter", "EndDocPrinter", "ClosePrinter", "AbortPrinter"):
        fn = getattr(ws, name); fn.argtypes = [wintypes.HANDLE]; fn.restype = wintypes.BOOL
    ws.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    ws.WritePrinter.restype = wintypes.BOOL
    hp = wintypes.HANDLE()
    if not ws.OpenPrinterW(queue, ctypes.byref(hp), None):
        raise OSError(ctypes.get_last_error(), "OpenPrinterW failed")
    started = False
    try:
        di = DOC_INFO_1W(title, None, "RAW")
        if not ws.StartDocPrinterW(hp, 1, ctypes.byref(di)):
            raise OSError(ctypes.get_last_error(), "StartDocPrinterW failed")
        started = True
        if not ws.StartPagePrinter(hp):
            raise OSError(ctypes.get_last_error(), "StartPagePrinter failed")
        written = wintypes.DWORD()
        data = ctypes.create_string_buffer(raw)
        if not ws.WritePrinter(hp, data, len(raw), ctypes.byref(written)) or written.value != len(raw):
            raise OSError(ctypes.get_last_error(), f"WritePrinter wrote {written.value}/{len(raw)} bytes")
        if not ws.EndPagePrinter(hp):
            raise OSError(ctypes.get_last_error(), "EndPagePrinter failed")
        if not ws.EndDocPrinter(hp):
            raise OSError(ctypes.get_last_error(), "EndDocPrinter failed")
        started = False
    finally:
        if started:
            ws.AbortPrinter(hp)
        ws.ClosePrinter(hp)


def render_preview(raw, target_id, config, printers):
    if config.get("preview_provider", "disabled") != "labelary":
        raise RuntimeError("External previews are disabled")
    p = printers[target_id]
    base = config.get("labelary_url", "https://api.labelary.com").rstrip("/")
    if not base.startswith("https://"):
        raise RuntimeError("Preview service must use HTTPS")
    url = base + f"/v1/printers/{p.get('label_dpmm',8)}dpmm/labels/{p.get('label_width_in',4)}x{p.get('label_height_in',6)}/0/"
    req = urllib.request.Request(url, data=raw, method="POST", headers={"Accept": "image/png", "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read(4 * 1024 * 1024 + 1)


class ArchiveServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"
    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, archive, token, config, capture=None):
        if address[0] != "127.0.0.1":
            raise ValueError("The archive server may bind only to 127.0.0.1")
        super().__init__(address, Handler)
        self.archive, self.token, self.config, self.capture = archive, token, config, capture


class Handler(BaseHTTPRequestHandler):
    server_version = "ComPortIntercept"
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        # Never log tokens or request bodies.
        print(fmt % args)

    def security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.security_headers(); self.end_headers(); self.wfile.write(body)

    def local_request(self):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "").lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        return not origin or origin.lower() in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def allowed(self):
        if not self.local_request():
            self.send_json({"error": "Invalid host or origin"}, 403); return False
        value = self.headers.get("Authorization", "")
        valid = value.startswith("Bearer ") and value[7:].isascii() and hmac.compare_digest(value[7:], self.server.token)
        if not valid:
            self.send_json({"error": "Authentication required"}, 401); return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.local_request():
            self.send_json({"error": "Invalid host or origin"}, 403); return
        if path in ("/", "/app.js", "/style.css"):
            file = ROOT / "web" / ({"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path])
            body = file.read_bytes()
            content_type = {"/": "text/html", "/app.js": "text/javascript", "/style.css": "text/css"}[path]
            self.send_response(200); self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.security_headers(); self.end_headers(); self.wfile.write(body); return
        if not self.allowed(): return
        if path == "/api/status":
            state = self.server.capture.status() if self.server.capture else {"connected": False}
            self.send_json({"ok": True, **state, "capture_connected": state.get("connected", False),
                            "capture_error": state.get("last_error"), "loss_events": self.server.archive.recent_losses(), "driver_restarts": self.server.archive.recent_restarts(),
                            "preview_provider": self.server.config.get("preview_provider", "disabled"),
                            "printers": list(self.server.archive.printers.values())}); return
        if path == "/api/jobs":
            query = parse_qs(urlsplit(self.path).query).get("q", [""])[0].strip()
            self.send_json(self.server.archive.list_jobs(query[:500])); return
        parts = path.strip("/").split("/")
        if len(parts) in (3, 4) and parts[:2] == ["api", "jobs"] and parts[2].isdigit():
            row = self.server.archive.get_job(int(parts[2]))
            if not row: self.send_json({"error": "Not found"}, 404); return
            if len(parts) == 4 and parts[3] == "preview":
                # This POST-like side effect is intentionally disallowed on GET.
                self.send_json({"error": "Use the preview action"}, 405); return
            if len(parts) == 3:
                row.pop("raw_path", None); self.send_json(row); return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if not self.allowed(): return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > MAX_HTTP_BODY: raise ValueError("Request body too large")
            body = self.rfile.read(size)
            path = urlsplit(self.path).path
            if path == "/api/ingest":
                if not self.server.config.get("enable_test_ingest", False):
                    self.send_json({"error": "Test ingestion is disabled"}, 403); return
                item = json.loads(body.decode("utf-8"))
                raw = base64.b64decode(item["base64"], validate=True)
                target = int(item.get("target_id", 1))
                if target not in self.server.archive.printers: raise ValueError("Unknown printer target")
                job = self.server.archive.save_demo(target, raw)
                self.send_json({"ok": True, "job_ids": [job]}); return
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "jobs"] or not parts[2].isdigit():
                self.send_json({"error": "Not found"}, 404); return
            row = self.server.archive.get_job(int(parts[2]))
            if not row: self.send_json({"error": "Not found"}, 404); return
            raw = self.server.archive.read_raw(row)
            if parts[3] == "preview":
                img = render_preview(raw, row["target_id"], self.server.config, self.server.archive.printers)
                if len(img) > 4 * 1024 * 1024: raise ValueError("Preview exceeds size limit")
                self.send_response(200); self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(img))); self.security_headers(); self.end_headers(); self.wfile.write(img); return
            if parts[3] == "reprint":
                attempt = self.server.archive.start_reprint(row)
                try:
                    raw_print(row["printer_name"], raw, f"ComPortIntercept Reprint #{row['id']}")
                except Exception as exc:
                    try:
                        self.server.archive.finish_reprint(attempt, row["id"], exc)
                    except Exception:
                        pass  # Preserve the original print error and warn about its uncertain effect.
                    self.send_json({"ok": False, "submitted": None, "error": f"Print failed or its outcome is uncertain: {exc}. Check the printer before retrying."}, 409); return
                try:
                    self.server.archive.finish_reprint(attempt, row["id"])
                except Exception:
                    self.send_json({"ok": False, "submitted": True, "error": "The spooler accepted this segment, but the audit record could not be updated. Check the printer before retrying."}, 500); return
                self.send_json({"ok": True, "message": "Submitted to the Windows spooler; physical output is not confirmed"}); return
            self.send_json({"error": "Not found"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 409)


def main():
    if CONFIG.get("listen_host", "127.0.0.1") != "127.0.0.1":
        raise RuntimeError("listen_host must be 127.0.0.1")
    # Bind first. A second instance must not touch the database or token ACLs.
    server = ArchiveServer(("127.0.0.1", int(CONFIG.get("listen_port", 8765))), None, None, CONFIG)
    capture = None
    try:
        token = secure_token(DATA)
        archive = Archive(DATA, PRINTERS)
        capture = CaptureService(archive, CONFIG)
        server.archive, server.token, server.capture = archive, token, capture
        capture.start()
        print(f"Archive running at http://127.0.0.1:{server.server_address[1]} (API token: data/private/api-token)")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if capture:
            capture.stop()
        server.server_close()


if __name__ == "__main__":
    main()

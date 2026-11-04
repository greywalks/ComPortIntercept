from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import sqlite3
import struct
import threading
import time
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JOBS = DATA / "jobs"
DB = DATA / "archive.db"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
for p in (DATA, JOBS):
    p.mkdir(parents=True, exist_ok=True)

PRINTERS = {int(p["target_id"]): p for p in CONFIG["printers"]}
CAPTURE_DEVICE = CONFIG.get("capture_device", r"\\.\ZebraUsbTap")
IOCTL_GET_NEXT = 0x226004

def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

def conn():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c

with conn() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS jobs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      captured_at TEXT NOT NULL,
      target_id INTEGER NOT NULL,
      printer_name TEXT NOT NULL,
      windows_port TEXT NOT NULL,
      byte_count INTEGER NOT NULL,
      sha256 TEXT NOT NULL,
      raw_path TEXT NOT NULL,
      text TEXT,
      origin TEXT NOT NULL DEFAULT 'Captured',
      parent_job_id INTEGER,
      reprint_count INTEGER NOT NULL DEFAULT 0,
      last_reprinted_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_time ON jobs(captured_at DESC);
    CREATE INDEX IF NOT EXISTS idx_jobs_sha ON jobs(sha256);
    """)

def decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")

def save_job(target_id: int, raw: bytes, origin="Captured", parent_job_id=None) -> int:
    p = PRINTERS.get(target_id, {"name": f"Target {target_id}", "windows_port": "Unknown"})
    digest = hashlib.sha256(raw).hexdigest()
    with conn() as c:
        cur = c.execute(
            """INSERT INTO jobs(captured_at,target_id,printer_name,windows_port,byte_count,sha256,raw_path,text,origin,parent_job_id)
               VALUES(?,?,?,?,?,?, '',?,?,?)""",
            (now(), target_id, p["name"], p["windows_port"], len(raw), digest, decode(raw), origin, parent_job_id),
        )
        job_id = int(cur.lastrowid)
        rel = f"jobs/{job_id:010d}.bin"
        (DATA / rel).write_bytes(raw)
        c.execute("UPDATE jobs SET raw_path=? WHERE id=?", (rel, job_id))
    return job_id

class Assembler:
    def __init__(self):
        self.buf = bytearray()
        self.lock = threading.Lock()
    def feed(self, chunk: bytes):
        out = []
        with self.lock:
            self.buf.extend(chunk)
            while True:
                b = bytes(self.buf)
                u = b.upper()
                start = u.find(b"^XA")
                if start < 0:
                    if len(self.buf) > 1024 * 1024:
                        out.append(bytes(self.buf))
                        self.buf.clear()
                    break
                if start:
                    out.append(bytes(self.buf[:start]))
                    del self.buf[:start]
                    u = bytes(self.buf).upper()
                end = u.find(b"^XZ", 3)
                if end < 0:
                    break
                end += 3
                out.append(bytes(self.buf[:end]))
                del self.buf[:end]
        return [x for x in out if x]

ASSEMBLERS = {tid: Assembler() for tid in PRINTERS}
capture_state = {"connected": False, "last_error": None, "last_capture_at": None}

def capture_loop():
    if os.name != "nt" or not CONFIG.get("capture_enabled", True):
        return
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateFileW = k32.CreateFileW
    CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    CreateFileW.restype = wintypes.HANDLE
    DeviceIoControl = k32.DeviceIoControl
    DeviceIoControl.argtypes = [wintypes.HANDLE,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
    DeviceIoControl.restype = wintypes.BOOL
    CloseHandle = k32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    INVALID = wintypes.HANDLE(-1).value
    while True:
        h = CreateFileW(CAPTURE_DEVICE, 0x80000000, 0x3, None, 3, 0, None)
        if h == INVALID:
            capture_state.update(connected=False, last_error=f"CreateFile failed: {ctypes.get_last_error()}")
            time.sleep(2)
            continue
        capture_state.update(connected=True, last_error=None)
        try:
            while True:
                buf = ctypes.create_string_buffer(1024 * 1024 + 32)
                got = wintypes.DWORD(0)
                ok = DeviceIoControl(h, IOCTL_GET_NEXT, None, 0, buf, len(buf), ctypes.byref(got), None)
                if not ok:
                    err = ctypes.get_last_error()
                    if err in (259, 234):
                        time.sleep(0.05)
                        continue
                    capture_state.update(connected=False, last_error=f"DeviceIoControl failed: {err}")
                    break
                if got.value < 16:
                    time.sleep(0.02)
                    continue
                target_id, length, _stamp = struct.unpack_from("<IIQ", buf.raw, 0)
                raw = bytes(buf.raw[16:16+length])
                capture_state["last_capture_at"] = now()
                for payload in ASSEMBLERS.setdefault(target_id, Assembler()).feed(raw):
                    save_job(target_id, payload)
        finally:
            CloseHandle(h)
        time.sleep(1)

def raw_print(queue: str, raw: bytes, title: str):
    if os.name != "nt":
        raise RuntimeError("Reprint requires Windows")
    class DOC_INFO_1W(ctypes.Structure):
        _fields_=[("pDocName",wintypes.LPWSTR),("pOutputFile",wintypes.LPWSTR),("pDatatype",wintypes.LPWSTR)]
    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
    hp = wintypes.HANDLE()
    if not ws.OpenPrinterW(queue, ctypes.byref(hp), None):
        raise OSError(ctypes.get_last_error(), "OpenPrinterW failed")
    try:
        di = DOC_INFO_1W(title, None, "RAW")
        if not ws.StartDocPrinterW(hp, 1, ctypes.byref(di)):
            raise OSError(ctypes.get_last_error(), "StartDocPrinterW failed")
        try:
            ws.StartPagePrinter(hp)
            written = wintypes.DWORD()
            data = ctypes.create_string_buffer(raw)
            if not ws.WritePrinter(hp, data, len(raw), ctypes.byref(written)) or written.value != len(raw):
                raise OSError(ctypes.get_last_error(), f"WritePrinter wrote {written.value}/{len(raw)} bytes")
            ws.EndPagePrinter(hp)
        finally:
            ws.EndDocPrinter(hp)
    finally:
        ws.ClosePrinter(hp)

def render_preview(raw: bytes, target_id: int) -> bytes:
    if CONFIG.get("preview_provider") != "labelary":
        raise RuntimeError("Preview disabled")
    p = PRINTERS[target_id]
    url = CONFIG.get("labelary_url","https://api.labelary.com").rstrip("/") + f"/v1/printers/{p.get('label_dpmm',8)}dpmm/labels/{p.get('label_width_in',4)}x{p.get('label_height_in',6)}/0/"
    req = urllib.request.Request(url, data=raw, method="POST", headers={"Accept":"image/png","Content-Type":"application/x-www-form-urlencoded","User-Agent":"ComPortIntercept/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()

INDEX = ROOT / "web" / "index.html"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args)
    def send_json(self, obj, status=200):
        b=json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u=urlparse(self.path)
        if u.path == "/":
            b=INDEX.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b); return
        if u.path == "/api/status":
            self.send_json({"ok":True,"capture_connected":capture_state["connected"],"capture_error":capture_state["last_error"],"last_capture_at":capture_state["last_capture_at"],"printers":list(PRINTERS.values())}); return
        if u.path == "/api/jobs":
            q=parse_qs(u.query).get("q",[""])[0].strip()
            sql="SELECT * FROM jobs"; params=[]
            if q:
                sql += " WHERE text LIKE ? OR printer_name LIKE ? OR windows_port LIKE ? OR origin LIKE ?"; n=f"%{q}%"; params=[n,n,n,n]
            sql += " ORDER BY id DESC LIMIT 500"
            with conn() as c: rows=[dict(r) for r in c.execute(sql,params).fetchall()]
            for r in rows: r.pop("raw_path",None)
            self.send_json(rows); return
        if u.path.startswith("/api/jobs/") and u.path.endswith("/preview"):
            job_id=int(u.path.split("/")[3])
            with conn() as c: row=c.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
            if not row: self.send_error(404); return
            try: img=render_preview((DATA/row["raw_path"]).read_bytes(), int(row["target_id"]))
            except Exception as e: self.send_json({"error":str(e)},422); return
            self.send_response(200); self.send_header("Content-Type","image/png"); self.send_header("Content-Length",str(len(img))); self.end_headers(); self.wfile.write(img); return
        if u.path.startswith("/api/jobs/"):
            job_id=int(u.path.split("/")[3])
            with conn() as c: row=c.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
            if not row: self.send_error(404); return
            d=dict(row); d["text"]=d.get("text") or ""; d.pop("raw_path",None); self.send_json(d); return
        self.send_error(404)
    def do_POST(self):
        u=urlparse(self.path)
        n=int(self.headers.get("Content-Length","0")); body=self.rfile.read(n) if n else b""
        if u.path == "/api/ingest":
            try:
                o=json.loads(body.decode()); raw=base64.b64decode(o["base64"]); tid=int(o.get("target_id",1))
                ids=[save_job(tid,p,"Demo/Test") for p in ASSEMBLERS.setdefault(tid,Assembler()).feed(raw)]
                if not ids and raw: ids=[save_job(tid,raw,"Demo/Test")]
                self.send_json({"ok":True,"job_ids":ids})
            except Exception as e: self.send_json({"error":str(e)},400)
            return
        if u.path.startswith("/api/jobs/") and u.path.endswith("/reprint"):
            job_id=int(u.path.split("/")[3])
            with conn() as c: row=c.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
            if not row: self.send_error(404); return
            try:
                raw=(DATA/row["raw_path"]).read_bytes(); raw_print(row["printer_name"],raw,f"ComPortIntercept Reprint #{job_id}")
                with conn() as c: c.execute("UPDATE jobs SET reprint_count=reprint_count+1,last_reprinted_at=? WHERE id=?",(now(),job_id))
                self.send_json({"ok":True})
            except Exception as e: self.send_json({"ok":False,"error":str(e)},409)
            return
        self.send_error(404)

def main():
    threading.Thread(target=capture_loop,daemon=True).start()
    host=CONFIG.get("listen_host","127.0.0.1"); port=int(CONFIG.get("listen_port",8765))
    print(f"ComPortIntercept running at http://{host}:{port}")
    ThreadingHTTPServer((host,port),Handler).serve_forever()

if __name__ == "__main__":
    main()

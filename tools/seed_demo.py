from __future__ import annotations
import base64
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
if not config.get('enable_test_ingest', False):
    raise SystemExit('Set enable_test_ingest to true and restart the archive before seeding demo records.')
token = (ROOT / 'data' / 'private' / 'api-token').read_text(encoding='ascii').strip()
labels = {
    1: b'^XA^FO50,50^A0N,42,42^FDGK420d Demo^FS^XZ',
    2: b'^XA^FO50,50^A0N,42,42^FDZP 500 Demo^FS^XZ',
}
for target_id, raw in labels.items():
    body = json.dumps({'target_id': target_id, 'base64': base64.b64encode(raw).decode('ascii')}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{int(config.get('listen_port', 8765))}/api/ingest",
        data=body, method='POST', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=10) as response:
        print(response.read().decode())

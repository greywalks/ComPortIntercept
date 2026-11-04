from __future__ import annotations
import base64
import json
import urllib.request

LABELS = {
    1: b"^XA^PW812^LL1218^FO50,50^A0N,42,42^FDGK420d / USB002 Demo^FS^FO50,120^A0N,32,32^FDOrder 100001^FS^FO50,200^BCN,100,Y,N,N^FD100001^FS^XZ",
    2: b"^XA^PW812^LL1218^FO50,50^A0N,42,42^FDZP 500 / USB007 Demo^FS^FO50,120^A0N,32,32^FDOrder 200001^FS^FO50,200^BCN,100,Y,N,N^FD200001^FS^XZ",
}

for target_id, raw in LABELS.items():
    body = json.dumps({"target_id": target_id, "base64": base64.b64encode(raw).decode("ascii")}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765/api/ingest", data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(resp.read().decode("utf-8"))

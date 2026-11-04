import base64
import json
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.request import urlopen as client_urlopen
from pathlib import Path
from unittest.mock import patch

from server.app import ArchiveServer, CaptureService, secure_token
from server.archive import Archive

PRINTERS = {1: {'name': 'Printer 1', 'windows_port': 'USB001'}}


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = Archive(self.tmp.name, PRINTERS)
        self.token = 'test-token-' + 'a' * 40
        self.config = {'preview_provider': 'disabled', 'enable_test_ingest': False}
        self.server = ArchiveServer(('127.0.0.1', 0), self.archive, self.token, self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()
    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {}, method='POST' if data is not None else 'GET')
        try:
            with client_urlopen(req, timeout=3) as r: return r.status, r.read()
        except urllib.error.HTTPError as e: return e.code, e.read()
    def auth(self): return {'Authorization': 'Bearer ' + self.token}

    def test_auth_host_and_origin_are_required(self):
        self.assertEqual(self.request('/api/status')[0], 401)
        self.assertEqual(self.request('/api/status', headers=self.auth())[0], 200)
        self.assertEqual(self.request('/api/status', headers={**self.auth(), 'Host':'attacker.example'})[0], 403)
        self.assertEqual(self.request('/api/status', headers={**self.auth(), 'Origin':'https://attacker.example'})[0], 403)
        job = self.archive.save_demo(1, b'^XA^XZ')
        with patch('server.app.raw_print') as printer:
            self.assertEqual(self.request(f'/api/jobs/{job}/reprint', b'x=1', {'Origin':'https://attacker.example'})[0], 403)
            printer.assert_not_called()

    def test_test_ingest_is_opt_in_and_does_not_parse_or_merge(self):
        body = json.dumps({'target_id':1, 'base64':base64.b64encode(b'^XAUNFINISHED').decode()}).encode()
        self.assertEqual(self.request('/api/ingest', body, self.auth())[0], 403)
        self.config['enable_test_ingest'] = True
        status, response = self.request('/api/ingest', body, self.auth())
        self.assertEqual(status, 200)
        job = self.archive.get_job(json.loads(response)['job_ids'][0])
        self.assertEqual(self.archive.read_raw(job), b'^XAUNFINISHED')
        self.assertEqual(self.archive.pending_chunks(), [])

    def test_preview_requires_explicit_post_and_is_disabled_by_default(self):
        job = self.archive.save_demo(1, b'^XA^XZ')
        with patch('server.app.urllib.request.urlopen') as external:
            self.assertEqual(self.request(f'/api/jobs/{job}/preview', headers=self.auth())[0], 405)
            self.assertEqual(self.request(f'/api/jobs/{job}/preview', b'', self.auth())[0], 409)
            external.assert_not_called()

    def test_reprint_failure_is_not_counted(self):
        job = self.archive.save_demo(1, b'^XA^XZ')
        with patch('server.app.raw_print', side_effect=OSError('spooler failed')):
            self.assertEqual(self.request(f'/api/jobs/{job}/reprint', b'', self.auth())[0], 409)
        self.assertEqual(self.archive.get_job(job)['reprint_count'], 0)
        with patch('server.app.raw_print') as printer:
            status, body = self.request(f'/api/jobs/{job}/reprint', b'', self.auth())
            self.assertEqual(status, 200)
            self.assertIn('not confirmed', json.loads(body)['message'])
            printer.assert_called_once()
        self.assertEqual(self.archive.get_job(job)['reprint_count'], 1)

    def test_only_loopback_binding_is_permitted(self):
        with self.assertRaises(ValueError): ArchiveServer(('0.0.0.0', 0), self.archive, self.token, self.config)


class TokenTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows ACL test')
    def test_windows_token_acl_and_restart(self):
        with tempfile.TemporaryDirectory() as root:
            token = secure_token(root)
            self.assertEqual(token, secure_token(root))
            path = Path(root) / 'private' / 'api-token'
            script = Path(root) / 'acl-check.ps1'
            script.write_text("(Get-Acl -LiteralPath $args[0]).Access | ForEach-Object { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value + ':' + $_.IsInherited }", encoding='utf-8')
            output = subprocess.check_output(['powershell.exe', '-NoProfile', '-File', str(script), str(path)], text=True)
            self.assertEqual(set(output.splitlines()), {'S-1-5-18:False', 'S-1-5-32-544:False'})

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions test')
    def test_token_is_stable_and_private(self):
        with tempfile.TemporaryDirectory() as root:
            token = secure_token(root)
            self.assertEqual(token, secure_token(root))
            self.assertEqual((Path(root)/'private').stat().st_mode & 0o777, 0o700)
            self.assertEqual((Path(root)/'private'/'api-token').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__': unittest.main()

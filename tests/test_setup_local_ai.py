import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from scripts.setup_local_ai import download


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name) / "artifact.bin"
        self.payload = b"portable local inference artifact" * 128

    def serve(self, *, interrupt=False, ignore_range=False):
        payload = self.payload
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requested_range = self.headers.get("Range")
                requests.append(requested_range)
                if interrupt and len(requests) == 1:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload[:37])
                    self.close_connection = True
                    return
                offset = int(requested_range.split("=")[1].split("-")[0]) if requested_range else 0
                resume = requested_range and not ignore_range
                body = payload[offset:] if resume else payload
                self.send_response(206 if resume else 200)
                self.send_header("Content-Length", str(len(body)))
                if resume:
                    self.send_header("Content-Range", f"bytes {offset}-{len(payload) - 1}/{len(payload)}")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def close():
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()

        self.addCleanup(close)
        return f"http://127.0.0.1:{server.server_port}/artifact", requests

    def test_interrupted_response_resumes_and_verifies_complete_artifact(self):
        url, requests = self.serve(interrupt=True)
        expected = hashlib.sha256(self.payload).hexdigest()
        with patch("scripts.setup_local_ai.sleep"), redirect_stdout(io.StringIO()):
            self.assertEqual(download(url, self.target, expected), expected)
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertEqual(requests, [None, "bytes=37-"])
        self.assertFalse(self.target.with_suffix(".bin.part").exists())

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        partial = self.target.with_suffix(".bin.part")
        partial.write_bytes(b"old partial content")
        url, _ = self.serve(ignore_range=True)
        download(url, self.target, hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.target.read_bytes(), self.payload)

    def test_bad_checksum_never_publishes_final_artifact(self):
        url, _ = self.serve()
        with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            download(url, self.target, "0" * 64)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.target.with_suffix(".bin.part").read_bytes(), self.payload)

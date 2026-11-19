"""回执客户端与独立回执服务的集成核对（内存服务，无需外网）。"""
import pathlib
import sys
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from audit.receipt_client import post_receipt, verify_remote_receipt
from support.receipt_service import ReceiptStore, create_server


class ReceiptClientTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReceiptStore(pathlib.Path(self.tmp.name) / "sink.sqlite3")
        self.server = create_server(("127.0.0.1", 0), self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
        self.tmp.cleanup()

    def test_post_and_verify_digest(self):
        payload = {"processing_fact": "P011", "confirmed_at": "2026-10-10T10:00:00+08:00"}
        posted = post_receipt(self.base, "RCPT-1", payload)
        self.assertTrue(posted["accepted"])
        record = verify_remote_receipt(self.base, "RCPT-1", payload)
        self.assertEqual(record["digest"], posted["digest"])

    def test_digest_mismatch_and_missing_are_rejected(self):
        post_receipt(self.base, "RCPT-2", {"a": 1})
        with self.assertRaises(ValueError):
            verify_remote_receipt(self.base, "RCPT-2", {"a": 2})
        with self.assertRaises(LookupError):
            verify_remote_receipt(self.base, "UNKNOWN", {})

    def test_drop_once_reply_still_verifiable(self):
        # 断连演练：POST 可能无响应，但 GET 复核能确认已接收
        record = post_receipt(self.base, "RCPT-DROP", {"x": 1}, drop_once=True)
        self.assertTrue(record["accepted"])
        self.assertEqual(verify_remote_receipt(self.base, "RCPT-DROP", {"x": 1})["digest"],
                         record["digest"])


if __name__ == "__main__":
    unittest.main()

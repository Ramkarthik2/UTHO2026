import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from server import api
from server.api import make_server
from tests import fixture


class ResourcePdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = make_server(port=0, db_path=fixture.db_path())
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        with urllib.request.urlopen(cls.base + "/api/export/resource-inventory.pdf") as r:
            cls.status = r.status
            cls.ctype = r.headers.get("Content-Type")
            cls.disposition = r.headers.get("Content-Disposition")
            cls.pdf = r.read()
        with urllib.request.urlopen(cls.base + "/api/data-sources") as r:
            cls.ds = json.loads(r.read())

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_route_returns_a_pdf_shown_inline(self):
        self.assertEqual(self.status, 200)
        self.assertEqual(self.ctype, "application/pdf")
        self.assertIn("inline", self.disposition)

    def test_file_is_a_well_formed_pdf(self):
        self.assertTrue(self.pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(self.pdf.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"/Type /Catalog", self.pdf)

    def test_page_numbers_match_page_count(self):
        count = int(re.search(rb"/Count (\d+)", self.pdf).group(1))
        self.assertGreater(count, 1)
        self.assertIn(f"Page {count} of {count}".encode(), self.pdf)

    def test_every_resource_is_listed_once_per_inventory_table(self):
        ids = [r["resource_id"] for r in self.ds["resources"]]
        self.assertEqual(self.ds["inventory"]["resource_count"], len(ids))
        for rid in ids:
            self.assertIn(rid.encode(), self.pdf, rid)

    def test_report_states_the_real_seed_and_disclaimer(self):
        self.assertIn(str(self.ds["inventory"]["seed"]).encode(), self.pdf)
        self.assertIn(b"Synthetic demo data", self.pdf)
        self.assertIn(b"retrieved from a live cloud provider", self.pdf)

    def test_data_sources_points_at_real_routes(self):
        self.assertEqual(self.ds["documents"]["inventory_pdf"], "/api/export/resource-inventory.pdf")
        self.assertEqual(self.ds["documents"]["inventory_csv"], "/api/export/resources.csv")

    def test_csv_download_still_returns_every_resource(self):
        with urllib.request.urlopen(self.base + "/api/export/resources.csv") as r:
            body = r.read().decode("utf-8").strip().splitlines()
            self.assertIn("attachment", r.headers.get("Content-Disposition"))
        self.assertEqual(len(body) - 1, self.ds["inventory"]["resource_count"])

    def test_failure_is_reported_not_sent_as_a_broken_file(self):
        with mock.patch.object(api.resource_pdf, "build_report", side_effect=RuntimeError("disk full")):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.base + "/api/export/resource-inventory.pdf")
            self.assertEqual(cm.exception.code, 500)
            payload = json.loads(cm.exception.read())
            self.assertIn("disk full", payload["error"])


if __name__ == "__main__":
    unittest.main()

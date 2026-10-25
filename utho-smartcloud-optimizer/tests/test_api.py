import csv
import io
import json
import threading
import unittest
import urllib.error
import urllib.request

from server.api import make_server
from tests import fixture


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = make_server(port=0, db_path=fixture.db_path())
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())

    def test_health_and_static(self):
        status, _, body = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["synthetic_data"])
        status, ctype, _ = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)

    def test_path_traversal_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/%2e%2e/server/config.py")
        self.assertEqual(cm.exception.code, 404)

    def test_overview_totals_agree_with_analysis(self):
        _, _, body = self.get("/api/overview")
        data = json.loads(body)
        self.assertEqual(data["kpis"]["resources"], 100)
        self.assertAlmostEqual(data["kpis"]["current_monthly"],
                               fixture.result()["totals"]["monthly_total"], places=1)

    def test_filter_changes_the_totals(self):
        _, _, body = self.get("/api/overview?environment=Production")
        filtered = json.loads(body)["kpis"]["resources"]
        self.assertLess(filtered, 100)
        self.assertGreater(filtered, 0)

    def test_overview_potential_matches_simulator_everything(self):
        _, _, body = self.get("/api/overview")
        overview_savings = json.loads(body)["kpis"]["potential_savings_monthly"]
        _, _, sim = self.get("/api/simulate?scope=everything")
        sim_savings = json.loads(sim)["plans"]["everything"]["savings_monthly"]
        self.assertAlmostEqual(overview_savings, sim_savings, places=1)

    def test_encoded_recommendation_id_resolves(self):
        status, _, body = self.get("/api/recommendations/rightsize%3Avm-0001")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["recommendation"]["proposed"], "gp-2x8")

    def test_decision_endpoint_validates_status(self):
        req = urllib.request.Request(self.base + "/api/recommendations/rightsize:vm-0001/decision",
                                     data=json.dumps({"status": "bogus"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)

    def test_settings_rejects_negative_threshold(self):
        req = urllib.request.Request(self.base + "/api/settings",
                                     data=json.dumps({"thresholds": {"rightsize_cpu_p95_max": -3}}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)

    def test_recommendations_csv_has_header_and_rows(self):
        status, ctype, body = self.get("/api/export/recommendations.csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", ctype)
        rows = list(csv.reader(io.StringIO(body.decode())))
        self.assertEqual(rows[0][0], "recommendation_id")
        self.assertGreater(len(rows), 10)

    def test_wide_metrics_export_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/export/metrics.csv")
        self.assertEqual(cm.exception.code, 400)

    def test_resource_metrics_export_covers_full_window(self):
        _, _, body = self.get("/api/export/metrics.csv?resource=vm-0001")
        rows = list(csv.reader(io.StringIO(body.decode())))
        self.assertEqual(len(rows) - 1, 2880)

    def test_summary_export_is_marked_synthetic(self):
        _, ctype, body = self.get("/api/export/summary.md")
        self.assertIn("text/markdown", ctype)
        self.assertIn("synthetic", body.decode().lower())


if __name__ == "__main__":
    unittest.main()

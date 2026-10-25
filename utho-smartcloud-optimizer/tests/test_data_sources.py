import json
import threading
import unittest
import urllib.request

from server.api import make_server
from server import analysis
from server import config as C
from tests import fixture


class DataSourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = make_server(port=0, db_path=fixture.db_path())
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        with urllib.request.urlopen(cls.base + "/api/data-sources") as r:
            cls.d = json.loads(r.read())
        with urllib.request.urlopen(cls.base + "/api/data-quality") as r:
            cls.dq = json.loads(r.read())

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_describes_the_synthetic_source(self):
        self.assertEqual(self.d["environment"], "Demo / synthetic data")
        self.assertIn("Demo data only", self.d["notice"])
        self.assertEqual(self.d["interval_minutes"], 15)
        self.assertIn("illustrative", self.d["pricing"].lower())
        self.assertIn("not cloud-provider quotes", self.d["pricing"].lower())

    def test_window_comes_from_real_timestamps(self):
        self.assertIsNotNone(self.d["window"]["start"])
        self.assertLess(self.d["window"]["start"], self.d["window"]["end"])
        firsts = [r["first_ts"] for r in self.d["resources"] if r["first_ts"] is not None]
        self.assertEqual(self.d["window"]["start"], analysis._iso(min(firsts)))
        self.assertEqual(self.d["window"]["end"], analysis._iso(max(r["last_ts"] for r in self.d["resources"] if r["last_ts"] is not None)))

    def test_last_analysis_is_set_after_a_run(self):
        self.assertTrue(self.d["last_analysis_at"])

    def test_summary_matches_resource_rows(self):
        s = self.d["summary"]
        self.assertEqual(s["total_rows_valid"], sum(r["valid"] for r in self.d["resources"]))
        self.assertEqual(s["invalid_rows"], sum(r["invalid"] for r in self.d["resources"]))
        self.assertEqual(s["duplicate_rows"], sum(r["duplicates"] for r in self.d["resources"]))
        self.assertEqual(s["missing_intervals"], sum(r["missing"] for r in self.d["resources"]))

    def test_missing_intervals_are_counted_once(self):
        s = self.d["summary"]
        self.assertEqual(s["missing_intervals"], s["expected_intervals_total"] - s["total_rows_valid"])

    def test_overall_completeness_is_consistent(self):
        s = self.d["summary"]
        self.assertAlmostEqual(s["overall_completeness"], s["total_rows_valid"] / s["expected_intervals_total"], places=4)
        self.assertTrue(0.0 <= s["overall_completeness"] <= 1.0)

    def test_insufficient_count_matches_flags(self):
        flagged = sum(1 for r in self.d["resources"] if r["insufficient_history"])
        self.assertEqual(self.d["summary"]["resources_insufficient_history"], flagged)
        self.assertGreater(flagged, 0)  # the demo data contains low-coverage resources

    def test_insufficient_flag_follows_threshold(self):
        th = self.d["thresholds"]
        for r in self.d["resources"]:
            if r["completeness"] < th["min_completeness"]:
                self.assertTrue(r["insufficient_history"])
                self.assertEqual(r["status"], "insufficient")
                self.assertTrue(any("minimum" in reason for reason in r["reasons"]))
            elif r["insufficient_history"]:
                # flagged only because of observed days; the reason must say so
                self.assertTrue(any("days observed" in reason for reason in r["reasons"]))
            else:
                self.assertNotEqual(r["status"], "insufficient")

    def test_resources_are_ordered_issues_first(self):
        rank = {"insufficient": 0, "issue": 1, "ok": 2}
        ranks = [rank[r["status"]] for r in self.d["resources"]]
        self.assertEqual(ranks, sorted(ranks))

    def test_each_resource_uses_expected_interval_count(self):
        for r in self.d["resources"]:
            self.assertEqual(r["expected_intervals"], C.EXPECTED_INTERVALS)
            self.assertAlmostEqual(r["completeness"], min(1.0, r["valid"] / r["expected_intervals"]), places=4)

    def test_matches_existing_data_quality_totals(self):
        self.assertEqual(self.d["summary"]["total_rows_valid"], self.dq["summary"]["total_rows_valid"])
        self.assertEqual(self.d["summary"]["missing_intervals"], self.dq["summary"]["missing_intervals"])


if __name__ == "__main__":
    unittest.main()

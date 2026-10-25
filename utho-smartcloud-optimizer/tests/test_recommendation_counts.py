import json
import threading
import unittest
import urllib.request

from server.api import make_server
from tests import fixture


class RecommendationCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = make_server(port=0, db_path=fixture.db_path())
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.res = cls._get("/api/resources")
        cls.sim = cls._get("/api/simulate?scope=everything")["plans"]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    @classmethod
    def _get(cls, path):
        with urllib.request.urlopen(cls.base + path) as r:
            return json.loads(r.read())

    def test_resource_summary_matches_cost_simulator_plans(self):
        s = self.res["summary"]
        self.assertEqual(s["open_recommendations"], self.sim["everything"]["recommendation_count"])
        self.assertEqual(s["open_low_risk"], self.sim["low_risk"]["recommendation_count"])

    def test_vm_counts_plus_other_resources_equal_the_total(self):
        s = self.res["summary"]
        per_vm = sum(r["recommendation_count"] for r in self.res["items"])
        self.assertEqual(per_vm, s["open_on_vms"])
        self.assertEqual(s["open_on_vms"] + s["open_on_other_resources"], s["open_recommendations"])

    def test_recs_column_counts_open_items_only(self):
        # Every per-resource count is open-only, so it can never exceed the open total.
        self.assertLessEqual(sum(r["recommendation_count"] for r in self.res["items"]),
                             self.res["summary"]["open_recommendations"])


if __name__ == "__main__":
    unittest.main()

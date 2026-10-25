import json
import threading
import unittest
import urllib.error
import urllib.request

from server import explain
from server.api import Service, ai_explain_view, make_server
from tests import fixture


class ExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = Service(fixture.db_path())
        cls.res = cls.svc.analysis()
        cls.wh = cls.res["settings"]["working_hours"]

    def test_rightsize_explanation_uses_the_real_numbers(self):
        rid = next(r["resource_id"] for r in self.res["recommendations"] if r["type"] == "rightsize")
        rec = next(r for r in self.res["recommendations"] if r["resource_id"] == rid and r["type"] == "rightsize")
        profile = self.res["profiles"][rid]
        lines = explain.explain_recommendation(rec, profile, self.wh)
        self.assertTrue(3 <= len(lines) <= 4)
        self.assertIn(f"{profile['cpu']['mean']:.1f}%", lines[0])
        self.assertIn(f"{profile['cpu_business_p95']:.1f}%", lines[1])
        self.assertIn(f"{profile['mem']['p99']:.1f}%", lines[2])
        self.assertIn(rec["current"], lines[3])
        self.assertIn(rec["proposed"], lines[3])

    def test_orphaned_resource_is_explained_without_cpu_claims(self):
        rec = next(r for r in self.res["recommendations"] if r["type"] == "orphan_volume")
        lines = explain.explain_recommendation(rec, None, self.wh)
        self.assertTrue(3 <= len(lines) <= 4)
        self.assertFalse(any("CPU" in line for line in lines))
        self.assertIn("Nothing is removed automatically", lines[-1])

    def test_collect_more_data_does_not_claim_a_saving(self):
        rec = next(r for r in self.res["recommendations"] if r["type"] == "collect_more_data")
        lines = explain.explain_recommendation(rec, None, self.wh)
        self.assertIn("asks for more data", lines[-1])
        self.assertNotIn("saving", lines[-1].replace("instead of claiming a saving", ""))


class AICommandApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = make_server(port=0, db_path=fixture.db_path())
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        with urllib.request.urlopen(cls.base + "/api/ai/resources") as r:
            cls.listing = json.loads(r.read())
        cls.open_ids = {r["resource_id"] for r in json.loads(
            urllib.request.urlopen(cls.base + "/api/recommendations?status=open").read())["items"]}

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_lists_every_resource_with_an_open_recommendation(self):
        listed = {x["resource_id"] for x in self.listing["items"]}
        self.assertEqual(listed, self.open_ids)
        self.assertEqual(self.listing["count"], len(listed))

    def test_list_is_sorted_by_savings(self):
        savings = [x["top_savings"] for x in self.listing["items"]]
        self.assertEqual(savings, sorted(savings, reverse=True))

    def test_explain_endpoint_returns_three_or_four_lines(self):
        rid = self.listing["items"][0]["resource_id"]
        with urllib.request.urlopen(self.base + "/api/ai/explain?resource=" + rid) as r:
            body = json.loads(r.read())
        self.assertEqual(body["resource_id"], rid)
        self.assertTrue(body["explanations"])
        for x in body["explanations"]:
            self.assertTrue(3 <= len(x["lines"]) <= 4)

    def test_one_explanation_per_open_recommendation(self):
        counts = {}
        for r in json.loads(urllib.request.urlopen(self.base + "/api/recommendations?status=open").read())["items"]:
            counts[r["resource_id"]] = counts.get(r["resource_id"], 0) + 1
        multi = [rid for rid, n in counts.items() if n >= 2]
        self.assertTrue(multi, "fixture should contain a resource with two open recommendations")
        for rid in multi:
            with urllib.request.urlopen(self.base + "/api/ai/explain?resource=" + rid) as r:
                body = json.loads(r.read())
            self.assertEqual(len(body["explanations"]), counts[rid], rid)
            savings = [x["rec_id"] for x in body["explanations"]]
            self.assertEqual(len(set(savings)), len(savings))

    def test_unknown_resource_is_not_found(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/api/ai/explain?resource=vm-does-not-exist")
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()

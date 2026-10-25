import unittest

from server.analysis import cost_anomalies
from server.pricing import vm_cost
from server import config as C
from tests import fixture


def R():
    return fixture.result()


class RightsizingTests(unittest.TestCase):
    def test_dev_underutilized_vm_is_downsized_with_exact_saving(self):
        rec = fixture.rec_by_id("rightsize:vm-0001")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["proposed"], "gp-2x8")
        self.assertEqual(rec["savings_monthly"], 2774.0)   # (7.60 - 3.80) x 730
        self.assertEqual(rec["risk"], "Low")
        self.assertGreaterEqual(rec["confidence"], 80)
        self.assertIn("95th-percentile", " ".join(rec["evidence"]))

    def test_production_peak_vm_is_not_downsized(self):
        self.assertIsNone(fixture.rec_by_id("rightsize:vm-0002"))
        prot = [p for p in R()["protected"] if p["resource_id"] == "vm-0002" and p["check"] == "rightsizing"]
        self.assertTrue(prot)
        self.assertIn("peak", " ".join(prot[0]["reasons"]).lower())

    def test_memory_constrained_vm_is_not_downsized(self):
        self.assertIsNone(fixture.rec_by_id("rightsize:vm-0003"))
        prot = [p for p in R()["protected"] if p["resource_id"] == "vm-0003"]
        self.assertTrue(any("memory" in " ".join(p["reasons"]).lower() for p in prot))

    def test_batch_vm_with_low_average_is_protected_by_peak_guard(self):
        self.assertIsNone(fixture.rec_by_id("rightsize:vm-0008"))

    def test_rightsizing_never_claims_savings_for_insufficient_data(self):
        rec = fixture.rec_by_id("collect_more_data:vm-0007")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["savings_monthly"], 0.0)
        self.assertLessEqual(rec["confidence"], 55)
        self.assertIsNone(fixture.rec_by_id("rightsize:vm-0007"))


class ScheduleTests(unittest.TestCase):
    def test_validated_dev_workload_gets_schedule_with_formula_savings(self):
        rec = fixture.rec_by_id("schedule_off_hours:vm-0004")
        self.assertIsNotNone(rec)
        expected = 6.8 * 730 * (1 - 50 / 168)
        self.assertAlmostEqual(rec["savings_monthly"], round(expected, 2), places=1)

    def test_production_batch_is_never_scheduled(self):
        self.assertIsNone(fixture.rec_by_id("schedule_off_hours:vm-0008"))


class IdleAndOrphanTests(unittest.TestCase):
    def test_consistently_idle_vm_gets_conditional_stop_test(self):
        rec = fixture.rec_by_id("idle_stop_test:vm-0010")
        self.assertIsNotNone(rec)
        self.assertTrue(rec["conditional_savings"])
        self.assertEqual(rec["savings_monthly"], 3.8 * 730)     # compute only
        self.assertIn("rightsize:vm-0010", rec["overlaps_with"])

    def test_orphan_volume_past_threshold_is_flagged_with_cost(self):
        rec = fixture.rec_by_id("orphan_volume:vol-orphan-0001")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["savings_monthly"], 500 * C.STORAGE_PER_GB_MONTH_INR)
        self.assertIsNone(fixture.rec_by_id("orphan_volume:vol-orphan-0002"))   # 5 days, below 14

    def test_unattached_ip_below_threshold_is_not_flagged(self):
        self.assertIsNone(fixture.rec_by_id("orphan_public_ip:ip-orphan-0002"))
        self.assertIsNotNone(fixture.rec_by_id("orphan_public_ip:ip-orphan-0001"))


class AnomalyTests(unittest.TestCase):
    def test_production_spike_flagged_on_spike_days(self):
        dates = {a["date"] for a in R()["cost_anomalies"] if a["environment"] == "Production"}
        self.assertIn("2026-10-22", dates)
        top = [a for a in R()["cost_anomalies"] if a["date"] == "2026-10-22"][0]["contributors"][0]
        self.assertEqual(top["resource_id"], "vm-0006")

    def test_low_variance_baseline_is_floored(self):
        profiles = {"x": {"id": "x", "name": "x", "environment": "Staging", "hourly": 1.0, "base": {"storage_gb": 0, "public_ip": False},
                          "avg_daily_gb": 0.0, "daily": [{"egress_gb": 0.0}] * 30}}
        th = dict(C.THRESHOLDS)
        self.assertEqual(cost_anomalies(profiles, th), [])   # flat series must not produce a flag


class AlertAndQualityTests(unittest.TestCase):
    def _alert(self, rtype, rid):
        return [a for a in R()["alerts"] if a["type"] == rtype and a["resource_id"] == rid]

    def test_expected_alerts(self):
        self.assertTrue(self._alert("cpu_spike", "vm-0006"))
        self.assertTrue(self._alert("storage_limit", "vm-0012"))
        self.assertTrue(self._alert("incomplete_metrics", "vm-0007"))

    def test_invalid_and_duplicate_rows_are_excluded_not_zeroed(self):
        dq = {r["resource_id"]: r for r in R()["data_quality"]["resources"]}
        self.assertEqual(dq["vm-0005"]["invalid"], 3)
        self.assertEqual(dq["vm-0005"]["duplicates"], 2)
        self.assertGreater(dq["vm-0005"]["valid"], 2800)
        self.assertGreater(dq["vm-0007"]["missing"], 1000)
        self.assertLess(dq["vm-0007"]["completeness"], 0.9)

    def test_recommendation_ids_are_unique(self):
        ids = [r["id"] for r in R()["recommendations"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_totals_match_profiles_plus_orphans(self):
        res = R()
        vm_sum = sum(p["cost"]["total"] for p in res["profiles"].values())
        self.assertAlmostEqual(res["totals"]["vm_total"], vm_sum, places=1)
        self.assertAlmostEqual(res["totals"]["monthly_total"],
                               res["totals"]["vm_total"] + res["totals"]["orphan_total"], places=1)

    def test_all_savings_are_non_negative(self):
        self.assertTrue(all(r["savings_monthly"] >= 0 for r in R()["recommendations"]))


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from pathlib import Path

from server import store, synth
from server.simulate import simulate
from tests import fixture


class SimulatorTests(unittest.TestCase):
    def test_overlapping_recommendations_are_not_double_counted(self):
        res = fixture.result()
        rightsize = fixture.rec_by_id("rightsize:vm-0010")
        stop = fixture.rec_by_id("idle_stop_test:vm-0010")
        plan = simulate(res, [rightsize, stop], "both")
        # Stop removes all VM compute (2774). Combined saving is 2774, not 1387 + 2774.
        profile_only = simulate(res, [stop], "stop")
        self.assertAlmostEqual(plan["savings_monthly"], profile_only["savings_monthly"] - 0, places=2)
        self.assertAlmostEqual(plan["overlap_adjustment"], -1387.0, places=2)

    def test_baseline_matches_analysis_totals(self):
        res = fixture.result()
        base = simulate(res, [], "baseline")
        self.assertAlmostEqual(base["baseline_monthly"], res["totals"]["monthly_total"], places=1)
        self.assertEqual(base["savings_monthly"], 0.0)

    def test_orphan_deletion_removes_only_that_cost(self):
        res = fixture.result()
        rec = fixture.rec_by_id("orphan_volume:vol-orphan-0001")
        sim = simulate(res, [rec], "orphan")
        self.assertAlmostEqual(sim["savings_monthly"], 3000.0, places=2)


class DecisionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "decisions.sqlite"
        synth.generate(self.path)

    def test_decision_survives_reconnect_and_is_audited(self):
        conn = store.connect(self.path)
        store.set_decision(conn, "rightsize:vm-0001", "accepted", "ok")
        conn.close()
        conn = store.connect(self.path)
        dec = store.decisions_map(conn)
        self.assertEqual(dec["rightsize:vm-0001"]["status"], "accepted")
        self.assertEqual(dec["rightsize:vm-0001"]["notes"], "ok")
        events = [a["event"] for a in store.audit_log(conn)]
        self.assertIn("decision:accepted", events)
        conn.close()

    def test_rejected_and_deferred_stay_in_history_after_reset(self):
        conn = store.connect(self.path)
        store.set_decision(conn, "a", "accepted", "")
        store.set_decision(conn, "b", "rejected", "not now")
        store.set_decision(conn, "c", "deferred", "later")
        removed = store.reset_accepted(conn)
        dec = store.decisions_map(conn)
        self.assertEqual(removed, 1)
        self.assertNotIn("a", dec)
        self.assertEqual(dec["b"]["status"], "rejected")
        self.assertEqual(dec["c"]["status"], "deferred")
        self.assertTrue(any(a["event"] == "decision:rejected" for a in store.audit_log(conn)))
        conn.close()

    def test_alert_acknowledgement_persists(self):
        conn = store.connect(self.path)
        store.ack_alert(conn, "abc123", "reviewed")
        conn.close()
        conn = store.connect(self.path)
        self.assertIn("abc123", store.acks_map(conn))
        conn.close()

    def test_settings_override_is_applied_and_unknown_keys_ignored(self):
        conn = store.connect(self.path)
        store.set_setting(conn, "thresholds", {"rightsize_cpu_p95_max": 5.0, "not_a_key": 1})
        eff = store.effective_settings(conn)
        self.assertEqual(eff["thresholds"]["rightsize_cpu_p95_max"], 5.0)
        self.assertNotIn("not_a_key", eff["thresholds"])
        conn.close()


if __name__ == "__main__":
    unittest.main()

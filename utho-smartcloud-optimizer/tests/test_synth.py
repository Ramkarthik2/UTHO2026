import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server import synth
from tests import fixture


def _metrics_digest(path: Path) -> str:
    conn = sqlite3.connect(path)
    h = hashlib.sha256()
    for row in conn.execute("SELECT * FROM metrics ORDER BY resource_id, ts"):
        h.update(repr(row).encode())
    conn.close()
    return h.hexdigest()


class SynthTests(unittest.TestCase):
    def test_generation_is_deterministic(self):
        d = Path(tempfile.mkdtemp())
        synth.generate(d / "a.sqlite")
        synth.generate(d / "b.sqlite")
        self.assertEqual(_metrics_digest(d / "a.sqlite"), _metrics_digest(d / "b.sqlite"))

    def test_inventory_size_and_named_demo_vms(self):
        conn = sqlite3.connect(fixture.db_path())
        n = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        names = {r[0] for r in conn.execute("SELECT name FROM resources")}
        conn.close()
        self.assertTrue(50 <= n <= 150)
        for required in ("dev-sandbox-api-01", "prod-checkout-api-01", "batch-reconcile-01",
                         "prod-pipeline-ingest-01", "dev-qa-regression-01"):
            self.assertIn(required, names)

    def test_thirty_days_at_fifteen_minutes(self):
        conn = sqlite3.connect(fixture.db_path())
        per_vm = conn.execute(
            "SELECT resource_id, COUNT(*) FROM metrics WHERE resource_id='vm-0001' GROUP BY resource_id").fetchone()
        conn.close()
        self.assertEqual(per_vm[1], 2880)

    def test_orphans_and_quality_issues_are_present(self):
        conn = sqlite3.connect(fixture.db_path())
        orphans = conn.execute("SELECT COUNT(*) FROM volumes WHERE resource_id IS NULL").fetchone()[0]
        bad = conn.execute("SELECT COUNT(*) FROM metrics WHERE resource_id='vm-0005' AND cpu_pct < 0").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(orphans, 4)
        self.assertGreaterEqual(bad, 1)


if __name__ == "__main__":
    unittest.main()

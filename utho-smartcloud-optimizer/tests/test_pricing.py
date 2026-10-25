import unittest

from server import config as C
from server.pricing import compute_monthly, inr, schedule_active_fraction, vm_cost


class FormattingTests(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(inr(1234567), "₹12,34,567")
        self.assertEqual(inr(100000), "₹1,00,000")
        self.assertEqual(inr(999), "₹999")
        self.assertEqual(inr(-45000), "-₹45,000")


class PricingTests(unittest.TestCase):
    def test_compute_uses_730_hour_month(self):
        self.assertEqual(compute_monthly(3.8), 3.8 * 730)

    def test_schedule_fraction_is_50_of_168_hours(self):
        self.assertAlmostEqual(schedule_active_fraction(C.DEFAULT_WORKING_HOURS), 50 / 168)

    def test_base_cost_components(self):
        base = {"hourly": 3.8, "storage_gb": 100, "public_ip": True, "egress_monthly": 0}
        c = vm_cost(base)
        self.assertEqual(c["compute"], round(3.8 * 730, 2))
        self.assertEqual(c["storage"], 100 * C.STORAGE_PER_GB_MONTH_INR)
        self.assertEqual(c["public_ip"], C.PUBLIC_IP_PER_MONTH_INR)
        self.assertEqual(c["total"], round(c["compute"] + c["storage"] + c["public_ip"], 2))

    def test_stopped_vm_stops_compute_but_keeps_storage_and_ip(self):
        base = {"hourly": 3.8, "storage_gb": 100, "public_ip": True, "egress_monthly": 0}
        stopped = vm_cost(base, {"stop": True})
        self.assertEqual(stopped["compute"], 0.0)
        self.assertEqual(stopped["storage"], 600.0)
        self.assertEqual(stopped["public_ip"], C.PUBLIC_IP_PER_MONTH_INR)

    def test_schedule_only_reduces_compute(self):
        base = {"hourly": 6.8, "storage_gb": 0, "public_ip": False, "egress_monthly": 0}
        scheduled = vm_cost(base, {"schedule": True})
        expected = 6.8 * 730 * (50 / 168)
        self.assertAlmostEqual(scheduled["compute"], expected, places=1)

    def test_resize_uses_catalogue_price(self):
        base = {"hourly": 7.6, "storage_gb": 0, "public_ip": False, "egress_monthly": 0}
        resized = vm_cost(base, {"resize_to": "gp-2x8"})
        self.assertEqual(resized["compute"], round(3.8 * 730, 2))


if __name__ == "__main__":
    unittest.main()

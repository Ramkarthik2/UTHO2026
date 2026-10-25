import unittest

from server.stats import longest_run_at_or_above, percentile, stdev, summarize


class PercentileTests(unittest.TestCase):
    def test_p95_of_1_to_100_uses_linear_interpolation(self):
        self.assertAlmostEqual(percentile(list(range(1, 101)), 95), 95.05)

    def test_median_of_even_length(self):
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)

    def test_single_value(self):
        self.assertEqual(percentile([5], 99), 5.0)

    def test_endpoints(self):
        self.assertEqual(percentile([3, 1, 2], 0), 1.0)
        self.assertEqual(percentile([3, 1, 2], 100), 3.0)

    def test_empty_raises_instead_of_returning_zero(self):
        with self.assertRaises(ValueError):
            percentile([], 50)

    def test_invalid_p_raises(self):
        with self.assertRaises(ValueError):
            percentile([1, 2], 101)


class SummaryTests(unittest.TestCase):
    def test_summary_fields(self):
        s = summarize([1, 2, 3, 4])
        self.assertEqual(s["mean"], 2.5)
        self.assertEqual(s["max"], 4.0)
        self.assertAlmostEqual(s["stdev"], 1.29, places=2)

    def test_stdev_single_value_is_zero(self):
        self.assertEqual(stdev([7]), 0.0)

    def test_longest_run(self):
        self.assertEqual(longest_run_at_or_above([96, 97, 10, 96, 96, 96, 1], 95), 3)
        self.assertEqual(longest_run_at_or_above([1, 2, 3], 95), 0)


if __name__ == "__main__":
    unittest.main()

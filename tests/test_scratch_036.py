"""SCRATCH for BRIEF-DEVWS-036 (proof PR, never merged): one added passing test."""
import unittest


class TestScratch036(unittest.TestCase):
    def test_scratch_plus_one(self):
        self.assertEqual(1 + 1, 2)


if __name__ == "__main__":
    unittest.main()

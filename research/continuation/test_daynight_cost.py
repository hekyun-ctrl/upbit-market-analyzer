"""Synthetic implementation regression checks, not strategy observations."""
import unittest
import math
from audit_daynight_cost import unit_counts,reprice,breakeven

class CostTests(unittest.TestCase):
    def test_flat(self):self.assertEqual(reprice(100,0,0,16),100)
    def test_single_entry(self):self.assertAlmostEqual(reprice(110,1,0,8),109.912)
    def test_reversal(self):self.assertAlmostEqual(reprice(110,0,1,8),109.824)
    def test_counts(self):self.assertEqual(unit_counts(3743,7485),(1,3742))
    def test_bad_counts(self):
        for n,t in [(1,3),(3,1),(1.5,2)]:
            with self.subTest(n=n,t=t),self.assertRaises(ValueError):unit_counts(n,t)
    def test_domain(self):
        with self.assertRaises(ValueError):reprice(100,0,1,5000)
    def test_unused_flip_domain(self):self.assertAlmostEqual(reprice(100,1,0,6000),40)
    def test_breakeven(self):
        for gross,n1,n2 in [(110,1,0),(200,0,5),(95015.45936450094,1,3742)]:
            b=breakeven(gross,n1,n2)
            self.assertTrue(math.isclose(reprice(gross,n1,n2,b),100,rel_tol=1e-10))
    def test_no_positive_edge(self):
        self.assertIsNone(breakeven(99,1,1));self.assertIsNone(breakeven(100,0,0))

if __name__=='__main__':unittest.main()

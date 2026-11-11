import unittest
import numpy as np
from run_decision_guard import select_candidate
from draper.model import anchored_profi
from test_model import diamond


class DecisionGuardTests(unittest.TestCase):
    def test_rejects_regressions_and_small_gains(self):
        samples = {"profi": [1.0]*9, "restored": [1.1]*9,
                   "calibrated": [0.995]*9, "restored-anchors": [1.2]*9}
        self.assertEqual(select_candidate(samples)["selected"], "profi")
        samples["restored-anchors"] = [0.9]*9
        self.assertEqual(select_candidate(samples)["selected"], "restored-anchors")
        samples["restored"][0] = float("nan")
        with self.assertRaises(ValueError):
            select_candidate(samples)

    def test_minimal_calibration_keeps_feasible_profile(self):
        system, truth = diamond()
        initial = system.vector(truth)
        result = anchored_profi(system, initial, [{"block": 1, "count": 80}])
        np.testing.assert_allclose(result, initial)
        result = anchored_profi(system, initial, [{"block": 1, "count": 70}])
        self.assertAlmostEqual((system.blocks @ result)[1], 70)
        self.assertLess(system.audit(result)["cfg_max_absolute_residual"], 1e-6)
        self.assertTrue(np.all(result >= 0))

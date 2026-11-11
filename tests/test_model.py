import copy
import unittest
import numpy as np

from draper.model import FlowSystem, alternative_anchors, anchored_profi, evaluate, fit, overlap, risks, select_anchors, simulate


def diamond():
    blocks = [{"id": block, "calls": [], "instructions": 2, "successor_edges": successors}
              for block, successors in enumerate(([1, 2], [3], [4], []))]
    edges = [{"src": source, "dst": target, "counter": counter}
             for source, target, counter in ((-1, 0, 0), (0, 1, 1), (0, 2, 2), (1, 3, 1), (2, 3, 2), (3, -1, 3))]
    graph = {"ir_id": "test", "functions": [{"name": "main", "closed_calls": False,
                                               "blocks": blocks, "edges": edges}]}
    system = FlowSystem(graph)
    counts = np.array([100, 80, 20, 80, 20, 100], dtype=float)
    return system, system.profile(counts, "test exact")


class FlowTests(unittest.TestCase):
    def test_exact_mapping_and_no_truth_in_observations(self):
        system, truth = diamond()
        self.assertEqual(system.audit(system.vector(truth))["cfg_max_absolute_residual"], 0)
        observations = simulate(system, truth, 0.25, 0.2, seed=42)
        self.assertEqual(observations, simulate(system, truth, 0.25, 0.2, seed=42))
        self.assertNotIn("edges", observations["functions"][0])
        self.assertNotIn("blocks", observations["functions"][0])

    def test_noisy_counts_recover_feasible_flow(self):
        system, truth = diamond()
        observations = simulate(system, truth, 0.25, 0, seed=42)
        initial = np.array([100, 95, 5, 95, 5, 100], dtype=float)
        result, audit = fit(system, observations, initial)
        self.assertLess(audit["cfg_max_absolute_residual"], 1e-6)
        self.assertTrue(np.all(result >= 0))
        self.assertLess(evaluate(system, truth, result)["block_relative_l1"], 0.5)

    def test_exact_anchor_is_respected(self):
        system, truth = diamond()
        observations = simulate(system, truth, 0.25, 0, seed=42)
        result, _ = fit(system, observations, system.vector(truth), anchors=[{"block": 1, "count": 80}])
        self.assertAlmostEqual((system.blocks @ result)[1], 80, places=6)
        projected = anchored_profi(system, np.array([100, 95, 5, 95, 5, 100]), [{"block": 1, "count": 80}])
        self.assertAlmostEqual((system.blocks @ projected)[1], 80, places=6)
        self.assertLess(system.audit(projected)["cfg_max_absolute_residual"], 1e-6)

    def test_missing_and_zero_are_different(self):
        system, truth = diamond()
        observations = simulate(system, truth, 0.25, 0, seed=42)
        observations["functions"][0]["samples"] = [0, 0, 0, 0]
        observations["functions"][0]["present"] = [False] * 4
        result, _ = fit(system, observations, np.zeros(system.edge_count))
        np.testing.assert_allclose(result, 0)
        profile, decisions = risks(system, observations, result, np.zeros((4, system.edge_count)))
        self.assertEqual(profile["functions"][0]["block_tags"][0], "unknown")
        self.assertTrue(all(entry["interval"] == [0.0, 1.0] for entry in decisions))

    def test_risky_threshold_and_budget(self):
        system, truth = diamond()
        observations = simulate(system, truth, 1, 0, seed=42)
        counts = system.vector(truth)
        replicates = np.array([[100, taken, 100-taken, taken, 100-taken, 100] for taken in (70, 75, 85, 90)])
        profile, decisions = risks(system, observations, counts, replicates)
        self.assertEqual(profile["functions"][0]["block_tags"][0], "risky")
        self.assertEqual(len(select_anchors(system, decisions, 1)), 1)
        self.assertEqual(select_anchors(system, decisions, 0), [])

    def test_selection_controls_and_profile_overlap(self):
        system, truth = diamond()
        estimate = system.vector(truth)
        self.assertEqual(alternative_anchors(system, estimate, 1, "frequency"), [1])
        self.assertEqual(alternative_anchors(system, estimate, 2, "random"),
                         alternative_anchors(system, estimate, 2, "random", seed=42))
        self.assertEqual(set(alternative_anchors(system, estimate, 100, "random")), {1, 2})
        self.assertAlmostEqual(overlap(estimate, estimate * 3), 1)
        self.assertEqual(overlap(estimate, np.zeros_like(estimate)), 0)
        self.assertEqual(evaluate(system, truth, estimate)["positive_block_coverage"], 1)

    def test_wrong_ir_is_rejected(self):
        system, truth = diamond()
        wrong = copy.deepcopy(truth)
        wrong["ir_id"] = "different"
        with self.assertRaises(ValueError):
            system.vector(wrong)

    def test_closed_call_constraints(self):
        system, truth = diamond()
        graph = copy.deepcopy(system.graph)
        graph["functions"][0]["blocks"][1]["calls"] = ["helper"]
        graph["functions"].append({"name": "helper", "closed_calls": True,
                                    "blocks": [{"id": 0, "calls": [], "successor_edges": []}],
                                    "edges": [{"src": -1, "dst": 0}, {"src": 0, "dst": -1}]})
        joint = FlowSystem(graph)
        self.assertEqual(joint.audit(np.r_[system.vector(truth), 80, 80])["call_max_absolute_residual"], 0)
        self.assertEqual(joint.audit(np.r_[system.vector(truth), 70, 70])["call_max_absolute_residual"], 10)


if __name__ == "__main__":
    unittest.main()

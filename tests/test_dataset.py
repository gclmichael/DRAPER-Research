import unittest

import numpy as np

from build_dataset import candidate_mapping, make_labels, probe_origins, split_programs
from measure_runtime import paired_improvement, renamed_main
from run_draper import check_reference_output


class DatasetTests(unittest.TestCase):
    def test_split_is_program_grouped_and_reproducible(self):
        programs = [f"program-{index}" for index in range(19)]
        first = split_programs(programs)
        self.assertEqual(first, split_programs(programs[::-1]))
        self.assertEqual([list(first.values()).count(name) for name in ("train", "validation", "test")], [13, 3, 3])

    def test_debug_matching_retains_ambiguous_origins(self):
        block = {"id": 0, "source_locations": [{"file": "sample.c", "line": 10, "column": 2}]}
        early = {"ir_id": "early", "functions": [{"name": "one", "blocks": [block]}, {"name": "two", "blocks": [block]}]}
        later = {"ir_id": "later", "functions": [{"name": "main", "blocks": [block]}]}
        self.assertEqual(len(candidate_mapping(early, later)["blocks"][0]["candidates"]), 2)

    def test_probe_origin_and_support_code_mask(self):
        text = "define i32 @main() {\ndraper.bb.2:\n call void @llvm.pseudoprobe(i64 -1, i64 3, i32 0, i64 -1)\n}"
        self.assertEqual(probe_origins(text)[0], {"function": "main", "block": 2, "guid": 2**64-1, "probe_id": 3, "type": 0})
        graph = {"ir_id": "test", "functions": [{"name": "main", "source_file": "support/main.c",
                                                  "blocks": [{"id": 0, "successor_edges": []}]}]}
        truth = {"functions": [{"blocks": [1], "edges": []}]}
        self.assertFalse(make_labels(graph, truth, "crc32")["blocks"][0]["train_mask"])

    def test_timing_pairs_and_entry_rename(self):
        result = paired_improvement([2, 4, 8], [1, 2, 4])
        self.assertAlmostEqual(result["speedup_percent"], 100)
        np.testing.assert_allclose(result["ci95_percent"], [100, 100])
        self.assertIn("@draper_original_main(", renamed_main("define i32 @main() { ret i32 0 }"))
        text = "@llvm.compiler.used = appending global [1 x ptr] [ptr @main]\ndefine i32 @main() { ret i32 0 }"
        self.assertIn("[ptr @draper_original_main]", renamed_main(text))
        with self.assertRaises(ValueError):
            renamed_main("declare i32 @main()")

    def test_llvm_reference_output(self):
        check_reference_output("answer 1.000000001\n", "answer 1.000000000\nexit 0\n")
        with self.assertRaises(RuntimeError):
            check_reference_output("answer 2\n", "answer 1\nexit 0\n")
        with self.assertRaises(RuntimeError):
            check_reference_output("wrong 1\n", "answer 1\nexit 0\n")
        with self.assertRaises(RuntimeError):
            check_reference_output("100000001\n", "100000000\nexit 0\n")


if __name__ == "__main__":
    unittest.main()

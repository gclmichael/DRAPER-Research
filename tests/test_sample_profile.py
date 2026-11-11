import unittest

from draper.sample_profile import parse_sample_profile, profile_statistics


class SampleProfileTests(unittest.TestCase):
    def test_contexts_zeros_checksums_and_targets(self):
        contexts = parse_sample_profile("""caller:100:2
 1: 0
 2.3: 8 ns::callee:7
 4: ns::callee:20
  1: 10
  2: helper:9
   1: 9
   !CFGChecksum: 999
  !CFGChecksum: 123
 5: ns::callee:30
  1: 20
  !CFGChecksum: 123
 !CFGChecksum: 456
other:0:0
 1: 0
 !CFGChecksum: 789
""")
        self.assertEqual(len(contexts), 5)
        self.assertEqual([c["parent_id"] for c in contexts], [None, 0, 1, 0, None])
        self.assertEqual(contexts[0]["cfg_checksum"], "456")
        self.assertEqual(contexts[0]["body"][0]["samples"], 0)
        self.assertEqual(contexts[0]["body"][1]["discriminator"], 3)
        self.assertEqual(contexts[0]["body"][1]["call_targets"],
                         [{"function": "ns::callee", "samples": 7}])
        self.assertEqual(contexts[3]["callsite"]["probe_id"], 5)
        self.assertEqual(profile_statistics(contexts)["explicit_zero_locations"], 2)

    def test_rejects_lossy_or_ambiguous_input(self):
        for text in ("", "f:1:1\n  1: 2", "f:1:1\n !Unknown: 2",
                     "f:1:1\n 1: -2", "f:1:1\n 1: 2\n 1: 3",
                     "f:1:1\n !CFGChecksum: 1\n !CFGChecksum: 2",
                     "f:1:1\nf:2:2", "f:1:1\n\t1: 2"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_sample_profile(text)


if __name__ == "__main__":
    unittest.main()

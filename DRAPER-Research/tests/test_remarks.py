from pathlib import Path
import tempfile
import unittest

from analyze_remarks import compare, read_events


class RemarkTests(unittest.TestCase):
    def test_cost_threshold_and_repeated_events(self):
        text = """--- !Passed
Pass: inline
Name: Inlined
Function: caller
DebugLoc: {File: example.c, Line: 5, Column: 3}
Args:
  - Callee: helper
  - Cost: '12'
  - Threshold: '40'
...
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "remarks.yaml"
            path.write_text(text)
            events = read_events(path)
        self.assertEqual(events[0]["threshold_minus_cost"], 28)
        difference = compare(events, events + events)["inline"]
        self.assertEqual(difference["candidate_only"], 1)
        missed = {**events[0], "type": "Missed"}
        self.assertEqual(compare(events, [missed])["inline"]["reference_only"], 1)

    def test_different_unroll_factors_are_different_transformations(self):
        text = """--- !Passed
Pass: loop-unroll
Name: PartialUnrolled
Function: work
DebugLoc: {File: example.c, Line: 9, Column: 2}
Args:
  - UnrollCount: '2'
...
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "remarks.yaml"
            path.write_text(text); first = read_events(path)
            path.write_text(text.replace("'2'", "'4'")); second = read_events(path)
        difference = compare(first, second)["loop-unroll"]
        self.assertEqual(first[0]["parameters"], {"UnrollCount": "2"})
        self.assertEqual((difference["reference_only"], difference["candidate_only"]), (1, 1))


if __name__ == "__main__":
    unittest.main()

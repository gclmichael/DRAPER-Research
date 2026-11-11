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


if __name__ == "__main__":
    unittest.main()

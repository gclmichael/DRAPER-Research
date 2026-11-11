import unittest

from prepare_cbench_bzip2 import parse_inputs


class CbenchInputsTest(unittest.TestCase):
    def test_extracts_input_without_executing_shell(self):
        records = parse_inputs("1\n=====\n1\n-z -c ../../data/1.dat > ftmp_out\n40\n")
        self.assertEqual(records, [{"id": 1, "path": "data/1.dat", "upstream_repetitions": 40}])

    def test_rejects_escape_and_wrong_count(self):
        with self.assertRaises(ValueError):
            parse_inputs("1\n=====\n1\n-z -c ../../../outside > ftmp_out\n40\n")
        with self.assertRaises(ValueError):
            parse_inputs("2\n=====\n1\n-z -c ../../data/1.dat > ftmp_out\n40\n")


if __name__ == "__main__":
    unittest.main()

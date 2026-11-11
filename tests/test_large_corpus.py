import unittest
from index_large_corpus import parse_counts,counter_locations


class CorpusCounterTests(unittest.TestCase):
    def test_counter_values_and_ir_indices(self):
        counts=parse_counts('f\n# Func Hash:\n123\n# Num Counters:\n2\n# Counter Values:\n10\n3\n')
        self.assertEqual(counts[0]['counts'],[10,3])
        text='@__profn_f = private constant [1 x i8] c"f"\ndefine void @f() {\nentry:\n call void @llvm.instrprof.increment(ptr @__profn_f, i64 123, i32 2, i32 1)\n}'
        rows=counter_locations(text)
        self.assertEqual(rows[0]['profile_name'],'f');self.assertEqual(rows[0]['counter'],1)
        weighted=counter_locations(text.replace('increment(', 'increment.step(').replace('i32 1)', 'i32 1, i64 %condition)'))
        self.assertEqual(weighted[0]['kind'],'weighted-counter')
        self.assertEqual(weighted[0]['increment'],'%condition')
        with self.assertRaises(ValueError):parse_counts('f\n# Func Hash:\n123\n# Num Counters:\n3\n# Counter Values:\n10\n3\n')

import unittest
from draper.evidence import transformation_evidence


class EvidenceTests(unittest.TestCase):
    def test_probe_provenance_retains_copies_and_unknowns(self):
        early='define void @f() {\ndraper.bb.0:\n call void @llvm.pseudoprobe(i64 7, i64 1, i32 0, i64 -1)\n}'
        optimized='define void @g() {\na:\n call void @llvm.pseudoprobe(i64 7, i64 1, i32 0, i64 -1)\nb:\n call void @llvm.pseudoprobe(i64 7, i64 1, i32 0, i64 -1)\n call void @llvm.pseudoprobe(i64 8, i64 2, i32 0, i64 -1)\n}'
        graph=transformation_evidence(early,optimized)
        self.assertEqual(graph['unique_origin_relations'],1)
        self.assertTrue(graph['relations'][0]['cross_function'])
        self.assertTrue(graph['relations'][0]['multiple_destinations'])
        self.assertEqual(graph['relations'][1]['origin_status'],'unknown')

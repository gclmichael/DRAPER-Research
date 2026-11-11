import unittest
import numpy as np
from test_model import diamond
from draper.model import fit,observation_operator,risks
from draper.transformed import relation,infer,intervals,coverage,mapping_score,calibrate_width,widen,exclusive_merges,propagate_bounds
from scipy import sparse


class TransformedTests(unittest.TestCase):
 def test_semantics_and_inverse(self):
  system,truth=diamond();exact=system.vector(truth)
  rows=[relation('identity',[0]),relation('inline',[1],[.5]),relation('clone',[2],[.25]),
        relation('merge-exclusive',[1,2]),relation('loop',[3],[.25])]
  o={'ir_id':'test','operator_rows':rows,'samples':[100,40,5,100,25],'present':[True]*5,'rate':1}
  np.testing.assert_allclose(observation_operator(system,o)@exact,o['samples'])
  estimate,_=fit(system,o,np.array([100,50,50,50,50,100.]))
  np.testing.assert_allclose(estimate,exact,atol=.01)
  with self.assertRaises(ValueError):relation('merge-sequential',[1,2])
  with self.assertRaises(ValueError):relation('clone',[1],[2])
  self.assertEqual(exclusive_merges(system,[[1,2]]),[(1,2)])
  self.assertEqual(exclusive_merges(system,[[0,1]]),[])

 def test_mapping_ambiguity_enters_intervals(self):
  system,truth=diamond();exact=system.vector(truth)
  rows=[relation('identity',[0]),relation('clone',[1],[.5])]
  o={'ir_id':'test','samples':[100,40],'present':[True,True],'rate':1}
  scenarios=[{'prior':.5,'operator_rows':rows},{'prior':.5,'operator_rows':[rows[0],relation('clone',[1],[1.])]}]
  result=intervals(system,o,exact,scenarios,repetitions=12)
  self.assertTrue(np.all(result['mapping_probabilities']>.1))
  self.assertGreater(np.ptp(result['draws'][:,1]),20)
  self.assertLess(system.audit(result['estimate'])['cfg_max_absolute_residual'],1e-5)
  self.assertGreaterEqual(coverage(result['block_intervals'],system.blocks@exact)['coverage'],.5)
  self.assertAlmostEqual(mapping_score([.5,.5],0)['brier'],.5)
  _,decisions=risks(system,{**o,'operator_rows':rows},result['estimate'],result['draws'])
  self.assertTrue(decisions)

 def test_large_counts_keep_normalized_scenario_weights(self):
  system,truth=diamond();exact=system.vector(truth)*1e8
  rows=[relation('identity',[i]) for i in range(4)]
  observed={'ir_id':'test','samples':(system.blocks@exact).tolist(),'present':[True]*4,'rate':1}
  scenarios=[{'prior':1/3,'operator_rows':rows} for _ in range(3)]
  result=infer(system,observed,exact,scenarios)
  self.assertAlmostEqual(float(result['mapping_probabilities'].sum()),1,places=14)
  np.random.default_rng(42).choice(3,p=result['mapping_probabilities'])

 def test_calibration_expands_zero_width_intervals(self):
  bounds=np.zeros((100,2,2));truth=np.full((100,2),7.)
  multiplier=calibrate_width(bounds,truth)
  self.assertEqual(coverage(widen(bounds[0],multiplier),truth[0])['coverage'],1.)

 def test_missing_branch_split_is_not_falsely_precise(self):
  system,truth=diamond();exact=system.vector(truth)
  observed={'ir_id':'test','samples':[100],'present':[True],'rate':1}
  scenarios=[{'prior':1.,'operator_rows':[relation('identity',[0])]}]
  result=intervals(system,observed,exact,scenarios,repetitions=8)
  self.assertIn(1,result['unidentified_blocks'])
  self.assertLessEqual(result['block_intervals'][1,0],1)
  self.assertGreaterEqual(result['block_intervals'][1,1],99)
  constraints=sparse.vstack([system.constraints,system.blocks.getrow(0)],format='csr')
  bounds=propagate_bounds(constraints,np.r_[np.zeros(system.constraints.shape[0]),1.],system.blocks,100.)
  np.testing.assert_allclose(bounds[0],[100,100],atol=1e-5)
  self.assertLessEqual(bounds[3,0],100)
  self.assertGreaterEqual(bounds[3,1],100)
  self.assertLessEqual(bounds[1,0],0)
  self.assertGreaterEqual(bounds[1,1],100)

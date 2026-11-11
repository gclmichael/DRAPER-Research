import unittest
import numpy as np
from test_model import diamond
from draper.decision_risk import bounded_projection, expected_loss, budgeted_anchors, replay_acceptance, linear_decisions


class RiskProjectionTests(unittest.TestCase):
    def test_branch_risk_matches_empirical_crossings(self):
        system,_=diamond();x=np.array([100,79,21,79,21,100.])
        draws=np.array([[100,k,100-k,k,100-k,100.] for k in (60,79,81,90)])
        decisions=linear_decisions(system,x,draws,include_stable=True)
        branch=next(d for d in decisions if d['kind']=='branch' and d['name']=='1')
        self.assertEqual(branch['probability_positive'],.5)
        np.testing.assert_allclose((branch['row']@draws.T).ravel(),[-.2,-.01,.01,.1],atol=1e-14)
        order=next(d for d in decisions if d['kind']=='successor-order')
        self.assertEqual(order['probability_positive'],1.)
        self.assertEqual(order['row'].nnz,2)

    def test_replay_protects_stable_decisions(self):
        branch=('branch','1');inline=('inline','call')
        base={branch:False,inline:True}
        draws=[{branch:True,inline:True} for _ in range(9)]+[base]
        decision={'kind':'branch','name':'1','cost':100,'probability_positive':.9}
        good=replay_acceptance(base,draws,{branch:True,inline:True},[decision])
        self.assertTrue(good['accepted'])
        bad=replay_acceptance(base,draws,{branch:True,inline:False},[decision])
        self.assertFalse(bad['accepted'])
        self.assertEqual(bad['changed_or_missing_stable_decisions'],[inline])

    def test_calibration_measures_branch_edge_and_uses_upper_cost(self):
        system,_=diamond();x=np.array([100,79,21,79,21,100.])
        decision={'kind':'branch','name':'2','block':0,'cost':100,'probability_positive':.5}
        selected=budgeted_anchors(system,[decision],x,total=1,dynamic_fraction=.1)
        self.assertEqual(selected['blocks'],[2])
        selected=budgeted_anchors(system,[decision],x,total=1,dynamic_fraction=.1,upper_counts=[100,79,100,100])
        self.assertEqual(selected['blocks'],[])
        selected=budgeted_anchors(system,[decision],x,total=1,dynamic_fraction=.1,upper_counts=[100,79,np.inf,100])
        self.assertEqual(selected['blocks'],[])

    def test_risk_changes_decision_with_hard_budget(self):
        system,_=diamond()
        x=np.array([100,79,21,79,21,100.])
        d={"row":np.array([-.8,1,0,0,0,0]),"threshold":0.,"desired":True,
           "probability_positive":.9,"cost":100.,"safe_margin":.5,"block":1}
        y,audit=bounded_projection(system,x,[d],.03,anchors=[{"block":0,"count":100}])
        self.assertTrue(audit["changed"])
        self.assertLess(expected_loss(y,[d]),expected_loss(x,[d]))
        self.assertLessEqual(np.abs(y-x).sum(),.03*x.sum()+1e-6)
        self.assertLess(system.audit(y)["cfg_max_absolute_residual"],1e-6)
        self.assertAlmostEqual((system.blocks@y)[0],100)
        z,_=bounded_projection(system,x,[d],0)
        np.testing.assert_array_equal(z,x)
        selection=budgeted_anchors(system,[d],x,dynamic_fraction=.01)
        self.assertEqual(selection["blocks"],[])
        preserved={**d,'desired':False}
        z,audit=bounded_projection(system,x,[d],.03,preserve=[preserved])
        self.assertFalse(audit['changed'])
        np.testing.assert_array_equal(z,x)

    def test_anchors_can_prevent_desired_flip(self):
        system,_=diamond();x=np.array([100,79,21,79,21,100.])
        d={"row":np.array([-.8,1,0,0,0,0]),"threshold":0.,"desired":True,
           "probability_positive":.9,"cost":100.,"safe_margin":.5,"block":1}
        y,a=bounded_projection(system,x,[d],.03,[{"block":0,"count":100},{"block":1,"count":79}])
        self.assertFalse(a["changed"])
        np.testing.assert_array_equal(y,x)

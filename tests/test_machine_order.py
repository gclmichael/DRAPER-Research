import unittest
from test_model import diamond
from run_core_risk import machine_order

class MachineOrderTests(unittest.TestCase):
 def test_removed_branch_has_no_layout_choice(self):
  system,_=diamond()
  name=system.functions[0]['name']
  text=f'''name: {name}
body: |
  bb.0.draper.bb.0:
    successors: %bb.1(0x60000000), %bb.2(0x20000000)
  bb.1.draper.bb.1:
    successors: %bb.3(0x80000000)
  bb.2.draper.bb.2:
    successors: %bb.3(0x80000000)
  bb.3.draper.bb.3:
    RET64
'''
  self.assertEqual(machine_order(text,system),{('successor-order','1:2'):True})
  self.assertEqual(machine_order(text.replace(', %bb.2(0x20000000)',''),system),{})
  self.assertEqual(machine_order(text.replace('bb.0.draper.bb.0:','bb.0.draper.bb.0.i:'),system),{})

if __name__=='__main__':unittest.main()

import tempfile,unittest,sys,subprocess
from pathlib import Path
from draper.workload import description,execute


class WorkloadTests(unittest.TestCase):
 def test_seekable_output_and_input(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'input').write_bytes(b'abc')
   code='import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stdout.buffer.flush(); sys.stdout.buffer.seek(0); sys.stdout.buffer.write(b"Z")'
   result=execute(sys.executable,{'cwd':d,'stdin':str(root/'input'),'arguments':['-c',code]})
   self.assertEqual(result.returncode,0)
   self.assertEqual(result.stdout,b'Zbc')
   self.assertGreater(result.elapsed_seconds,0)
 def test_timeout_terminates_child(self):
  with self.assertRaises(subprocess.TimeoutExpired):
   execute(sys.executable,{'arguments':['-c','import time;time.sleep(60)']},timeout=.05)
 def test_upstream_arguments_and_tolerance(self):
  with tempfile.TemporaryDirectory() as d:
   test=Path(d)/'p.test';test.write_text('RUN: cd %S ; %S/p --arg < input\nVERIFY: %b/fpcmp-target -r 0.025 %o %S/ref\n')
   result=description(test,d,d)
   self.assertEqual(result['arguments'],['--arg'])
   self.assertEqual(result['checks'][0][1:3],['-r','0.025'])

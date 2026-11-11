import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from draper.model import FlowSystem


ROOT = Path(__file__).resolve().parents[1]

# 两个 switch 分支共享目标块，用于检查关键边拆分后是否仍能区分计数。
MODULE = """
define internal i32 @helper(i32 %value) {
entry:
  %next = add i32 %value, 1
  ret i32 %next
}
define i32 @main() {
entry:
  br label %loop
loop:
  %index = phi i32 [0, %entry], [%next, %join]
  switch i32 %index, label %other [i32 0, label %shared
                                  i32 1, label %shared
                                  i32 2, label %second]
shared:
  br label %join
second:
  br label %join
other:
  br label %join
join:
  %next = call i32 @helper(i32 %index)
  %again = icmp ult i32 %next, 10
  br i1 %again, label %loop, label %exit
exit:
  ret i32 0
}
"""


class LLVMBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(shutil.which(tool) for tool in ("clang++", "clang", "opt", "llvm-config", "llvm-profdata")):
            raise unittest.SkipTest("LLVM development tools required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="draper-test-")
        cls.directory = Path(cls.temporary.name)
        cls.bridge = cls.directory / "draper-llvm"
        flags = shlex.split(cls.run_command(["llvm-config", "--cxxflags", "--ldflags", "--libs",
                                             "core", "passes", "analysis", "profiledata", "transformutils", "irreader"]))
        cls.run_command(["clang++", ROOT / "draper/llvm_bridge.cpp", *flags, "-O1", "-o", cls.bridge])
        cls.early = cls.directory / "early.ll"
        cls.early.write_text(MODULE)
        cls.canonical = cls.directory / "canonical.ll"
        cls.graph_path = cls.directory / "graph.json"
        cls.run_command([cls.bridge, "prepare", cls.early, f"--output={cls.canonical}",
                         f"--graph={cls.graph_path}", f"--instrumented={cls.directory / 'full.ll'}"])
        cls.collect("full")
        cls.run_command([cls.bridge, "truth", cls.canonical, f"--graph={cls.graph_path}",
                         f"--profile={cls.directory / 'full.profdata'}", f"--output={cls.directory / 'truth.json'}"])
        cls.graph = json.loads(cls.graph_path.read_text())
        cls.truth = json.loads((cls.directory / "truth.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @staticmethod
    def run_command(command, environment=None):
        result = subprocess.run([str(part) for part in command], capture_output=True, text=True,
                                env={**os.environ, **(environment or {})}, timeout=90)
        if result.returncode:
            raise RuntimeError(f"{command}\n{result.stderr}")
        return result.stdout

    @classmethod
    def collect(cls, name):
        cls.run_command(["opt", "-passes=instrprof", cls.directory / f"{name}.ll", "-o", cls.directory / f"{name}.bc"])
        cls.run_command(["clang", "-O3", "-c", cls.directory / f"{name}.bc", "-o", cls.directory / f"{name}.o"])
        cls.run_command(["clang", cls.directory / f"{name}.o", "-fprofile-instr-generate", "-o", cls.directory / name])
        cls.run_command([cls.directory / name], {"LLVM_PROFILE_FILE": str(cls.directory / f"{name}.profraw")})
        cls.run_command(["llvm-profdata", "merge", cls.directory / f"{name}.profraw", "-o", cls.directory / f"{name}.profdata"])

    def test_switch_edges_and_direct_calls(self):
        system = FlowSystem(self.graph)
        edges = system.vector(self.truth)
        self.assertEqual(system.audit(edges)["cfg_max_absolute_residual"], 0)
        self.assertEqual(system.audit(edges)["call_max_absolute_residual"], 0)
        main_index = next(index for index, entry in enumerate(self.graph["functions"]) if entry["name"] == "main")
        function = self.graph["functions"][main_index]
        switch = next(block for block in function["blocks"] if len(block["successor_edges"]) == 4)
        observed = [self.truth["functions"][main_index]["edges"][index] for index in switch["successor_edges"]]
        self.assertEqual(observed, [7, 1, 1, 1])
        helper = next(entry for entry in self.truth["functions"] if entry["name"] == "helper")
        self.assertEqual(helper["blocks"], [10])

    def test_exception_edges_are_measured(self):
        source=self.directory/'exception.cpp'
        source.write_text('int f(int x) { if(x) throw x; return 0; }\nint main(){int sum=0; for(int i=0;i<3;++i){try {sum+=f(i);}catch(int n){sum+=n;}} return sum!=3;}')
        early=self.directory/'exception.ll';graph=self.directory/'exception-graph.json'
        canonical=self.directory/'exception-canonical.ll'
        self.run_command(['clang++','-O0','-Xclang','-disable-O0-optnone','-S','-emit-llvm',source,'-o',early])
        self.run_command([self.bridge,'prepare',early,f'--output={canonical}',f'--graph={graph}',f'--instrumented={self.directory / "exception-full.ll"}'])
        self.run_command(['opt','-passes=instrprof',self.directory/'exception-full.ll','-o',self.directory/'exception.bc'])
        self.run_command(['clang++','-O3',self.directory/'exception.bc','-fprofile-instr-generate','-o',self.directory/'exception'])
        self.run_command([self.directory/'exception'],{'LLVM_PROFILE_FILE':str(self.directory/'exception.profraw')})
        self.run_command(['llvm-profdata','merge',self.directory/'exception.profraw','-o',self.directory/'exception.profdata'])
        truth=self.directory/'exception-truth.json'
        self.run_command([self.bridge,'truth',canonical,f'--graph={graph}',f'--profile={self.directory / "exception.profdata"}',f'--output={truth}'])
        system=FlowSystem(json.loads(graph.read_text()))
        self.assertEqual(system.audit(system.vector(json.loads(truth.read_text())))['cfg_max_absolute_residual'],0)
        self.assertTrue(any(e.get('counter_subtract',-1)>=0 for f in system.functions for e in f['edges']))

    def test_nonlocal_return_flow(self):
        source=self.directory/'jump.c'
        source.write_text('#include <setjmp.h>\njmp_buf b; void escape(void){longjmp(b,1);} int main(void){if(!setjmp(b)) escape(); return 0;}')
        early=self.directory/'jump.ll';graph=self.directory/'jump-graph.json';canonical=self.directory/'jump-canonical.ll'
        self.run_command(['clang','-O0','-Xclang','-disable-O0-optnone','-S','-emit-llvm',source,'-o',early])
        self.run_command([self.bridge,'prepare',early,f'--output={canonical}',f'--graph={graph}',f'--instrumented={self.directory / "jump-full.ll"}','--nonlocal-flow'])
        self.collect('jump-full')
        truth=self.directory/'jump-truth.json'
        self.run_command([self.bridge,'truth',canonical,f'--graph={graph}',f'--profile={self.directory / "jump-full.profdata"}',f'--output={truth}'])
        system=FlowSystem(json.loads(graph.read_text()));counts=json.loads(truth.read_text())
        self.assertEqual(system.audit(system.vector(counts))['cfg_max_absolute_residual'],0)
        self.assertTrue(any(c['edges'][i]>0 for f,c in zip(system.functions,counts['functions']) for i,e in enumerate(f['edges']) if e.get('nonlocal')))

    def test_sparse_measurement_and_llvm_consumption(self):
        selection = self.directory / "selected.json"
        selection.write_text(json.dumps({"ir_id": self.graph["ir_id"], "blocks": [0]}))
        self.run_command([self.bridge, "calibrate", self.canonical, f"--graph={self.graph_path}",
                          f"--selection={selection}", f"--output={self.directory / 'sparse.ll'}"])
        self.collect("sparse")
        anchors = self.directory / "anchors.json"
        self.run_command([self.bridge, "read-anchors", self.canonical, f"--graph={self.graph_path}",
                          f"--selection={selection}", f"--profile={self.directory / 'sparse.profdata'}", f"--output={anchors}"])
        self.assertEqual(json.loads(anchors.read_text())["anchors"], [{"block": 0, "count": 10}])
        annotated = self.directory / "annotated.ll"
        decisions = self.directory / "decisions.json"
        self.run_command([self.bridge, "annotate", self.canonical, f"--graph={self.graph_path}",
                          f"--profile={self.directory / 'truth.json'}", f"--output={annotated}", f"--decisions={decisions}"])
        self.assertIn('!"branch_weights"', annotated.read_text())
        self.assertTrue(any(entry["hot"] for entry in json.loads(decisions.read_text())["decisions"]))
        self.run_command(["clang", "-O3", annotated, "-o", self.directory / "annotated"])
        self.run_command([self.directory / "annotated"])

    def test_optimized_probe_occurrences(self):
        early=self.directory/'probe-early.ll';late=self.directory/'probe-late.ll'
        graph=self.directory/'probe-map.json';counts=self.directory/'probe-counts.json'
        self.run_command(['opt','-passes=pseudo-probe','-S',self.canonical,'-o',early])
        self.run_command(['opt','-passes=default<O3>','-S',early,'-o',late])
        self.run_command([self.bridge,'probe-prepare',late,f'--graph={graph}',f'--output={self.directory / "probe-full.ll"}'])
        self.collect('probe-full')
        self.run_command([self.bridge,'probe-truth',late,f'--graph={graph}',f'--profile={self.directory / "probe-full.profdata"}',f'--output={counts}'])
        rows=json.loads(counts.read_text())['occurrences']
        self.assertTrue(rows)
        self.assertTrue(all(row['count']>=0 for row in rows))
        self.assertTrue(any(row['count']>0 for row in rows))
        expected=sum(len(f['probes']) for f in json.loads(graph.read_text())['functions'])
        self.assertEqual(len(rows),expected)

    def test_uniform_conservative_removes_branch_weights(self):
        annotated = self.directory / "conservative.ll"
        self.run_command([self.bridge, "annotate", self.canonical, f"--graph={self.graph_path}",
                          f"--profile={self.directory / 'truth.json'}", f"--output={annotated}",
                          "--drop-branch-weights"])
        self.assertNotIn('!"branch_weights"', annotated.read_text())
        self.assertIn('!"function_entry_count"', annotated.read_text())

    def test_compiler_consumers_are_recorded(self):
        decisions = self.directory / 'consumers.json'
        self.run_command([self.bridge, 'annotate', self.canonical, f'--graph={self.graph_path}',
                          f'--profile={self.directory / "truth.json"}', f'--output={self.directory / "consumers.ll"}',
                          f'--decisions={decisions}', '--consumer-decisions'])
        records = json.loads(decisions.read_text())['consumer_decisions']
        self.assertTrue(any(r['kind'] == 'inline' and r['callee'] == 'helper' for r in records))
        self.assertTrue(any(r['kind'] == 'block-frequency' and r['relative_frequency'] > 0 for r in records))

    def test_recursive_pure_function_retains_every_counter_update(self):
        source = self.directory / "recursive.ll"
        source.write_text('''
define i32 @fib(i32 %depth) noinline memory(none) {
entry:
  %small = icmp sle i32 %depth, 1
  br i1 %small, label %base, label %recurse
base:
  ret i32 1
recurse:
  %first_depth = sub i32 %depth, 1
  %second_depth = sub i32 %depth, 2
  %first = call i32 @fib(i32 %first_depth) memory(none)
  %second = call i32 @fib(i32 %second_depth) memory(none)
  %sum = add i32 %first, %second
  ret i32 %sum
}
define i32 @main() {
entry:
  %value = call i32 @fib(i32 9) memory(none)
  %wrong = icmp ne i32 %value, 55
  %result = zext i1 %wrong to i32
  ret i32 %result
}
''')
        canonical = self.directory / "recursive-canonical.ll"
        graph = self.directory / "recursive-graph.json"
        self.run_command([self.bridge, "prepare", source, f"--output={canonical}", f"--graph={graph}",
                          f"--instrumented={self.directory / 'recursive-full.ll'}"])
        self.collect("recursive-full")
        truth = self.directory / "recursive-truth.json"
        self.run_command([self.bridge, "truth", canonical, f"--graph={graph}",
                          f"--profile={self.directory / 'recursive-full.profdata'}", f"--output={truth}"])
        counts = json.loads(truth.read_text())
        fib = next(function for function in counts["functions"] if function["name"] == "fib")
        self.assertEqual(fib["blocks"][0], 109)
        system = FlowSystem(json.loads(graph.read_text()))
        self.assertEqual(system.audit(system.vector(counts))["cfg_max_absolute_residual"], 0)


if __name__ == "__main__":
    unittest.main()

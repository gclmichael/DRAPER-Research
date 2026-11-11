#!/usr/bin/env bash
set -euo pipefail
cd /data/chaoni/chengli/draper-node3
export PATH=/data/chaoni/chengli/tools/draper-env/bin:$PATH
export LD_LIBRARY_PATH=/data/chaoni/chengli/tools/draper-env/lib
export TMPDIR=/data/chaoni/chengli/draper-node3/tmp
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
python - <<'PY'
import json,time
from pathlib import Path
p=Path('data/all-core-v2/summary.json')
for _ in range(1440):
    try:
        if p.exists() and json.loads(p.read_text())['status']=='finished':break
    except json.JSONDecodeError:pass
    time.sleep(10)
else:raise SystemExit('Previous batch did not finish')
PY
python run_core_risk.py data/transformed-aha-v4/experiment --output data/transformed-core-v3 --bridge data/probe-tools-v1/draper-llvm --fresh-calibration > state/transformed-core-v3.log 2>&1
python measure_runtime.py data/transformed-core-v3 --variants calibrated risk-projected --rounds 15 --seconds .3 > state/transformed-core-v3-timing.log 2>&1
python audit_core_results.py data/transformed-aha-v4/experiment data/transformed-core-v3 > state/transformed-core-v3-audit.log 2>&1
python -m unittest discover -s tests -v > state/nonlocal-tests.log 2>&1
python - <<'PY'
import subprocess,shlex
from pathlib import Path
p=Path('data/nonlocal-tools-v1');p.mkdir()
flags=shlex.split(subprocess.check_output(['llvm-config','--cxxflags','--ldflags','--libs','core','passes','analysis','profiledata','transformutils','irreader'],text=True))
subprocess.run(['clang++','draper/llvm_bridge.cpp',*flags,'-O1','-o',str(p/'draper-llvm')],check=True)
PY
python collect_all_flows.py data/industrial-corpus-v1 --bridge data/nonlocal-tools-v1/draper-llvm --output data/nonlocal-flows-v1 --programs anagram lua --nonlocal-flow > state/nonlocal-flows-v1.log 2>&1
python run_all_core.py data/nonlocal-flows-v1 --bridge data/nonlocal-tools-v1/draper-llvm --output data/nonlocal-core-v1 > state/nonlocal-core-v1.log 2>&1
python build_large_corpus.py --source data/large-sources/test-suite-23.1.5.src --output data/polybench-corpus-v1 --subdirectories SingleSource/Benchmarks/Polybench > state/polybench-build-v1.log 2>&1
python index_large_corpus.py data/polybench-corpus-v1 --name paired-records-v2 > state/polybench-index-v1.log 2>&1
python validate_large_corpus.py data/polybench-corpus-v1 > state/polybench-validation-v1.log 2>&1
python collect_all_flows.py data/polybench-corpus-v1 --bridge data/nonlocal-tools-v1/draper-llvm --output data/polybench-flows-v1 > state/polybench-flows-v1.log 2>&1
python run_all_core.py data/polybench-flows-v1 --bridge data/nonlocal-tools-v1/draper-llvm --output data/polybench-core-v1 --transformed > state/polybench-core-v1.log 2>&1

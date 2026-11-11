#!/usr/bin/env bash
set -euo pipefail
cd /data/chaoni/chengli/draper-node3
export PATH=/data/chaoni/chengli/tools/draper-env/bin:$PATH
export LD_LIBRARY_PATH=/data/chaoni/chengli/tools/draper-env/lib
export TMPDIR=/data/chaoni/chengli/draper-node3/tmp
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
python -m unittest discover -s tests -v > state/expanded-v2-tests.log 2>&1
python - <<'PY'
from pathlib import Path
import shutil,subprocess,shlex
root=Path.cwd();snapshot=root/'data/expanded-implementation-v2';snapshot.mkdir()
for path in root.glob('*.py'):shutil.copy2(path,snapshot/path.name)
for name in ('draper','fixtures'):shutil.copytree(root/name,snapshot/name,ignore=shutil.ignore_patterns('__pycache__'))
flags=shlex.split(subprocess.check_output(['llvm-config','--cxxflags','--ldflags','--libs','core','passes','analysis','profiledata','transformutils','irreader'],text=True))
subprocess.run(['clang++',str(snapshot/'draper/llvm_bridge.cpp'),*flags,'-O1','-o',str(snapshot/'draper-llvm')],check=True)
PY
cd data/expanded-implementation-v2
python collect_all_flows.py /data/chaoni/chengli/draper-node3/data/industrial-corpus-v1 --bridge ./draper-llvm --output /data/chaoni/chengli/draper-node3/data/all-flows-v2 > ../../state/all-flows-v2.log 2>&1
python run_all_core.py /data/chaoni/chengli/draper-node3/data/all-flows-v2 --bridge ./draper-llvm --output /data/chaoni/chengli/draper-node3/data/all-core-v2 > ../../state/all-core-v2.log 2>&1

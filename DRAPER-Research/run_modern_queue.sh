#!/usr/bin/env bash
set -euo pipefail
cd /data/chaoni/chengli/draper-node3
export PATH=/data/chaoni/chengli/tools/draper-env/bin:$PATH
export LD_LIBRARY_PATH=/data/chaoni/chengli/tools/draper-env/lib
export TMPDIR=/data/chaoni/chengli/draper-node3/tmp
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
python - <<'PY'
import os,time
# 前一批已经在本工作目录启动；保持大型实验顺序执行。
while True:
    try:os.kill(1451078,0)
    except ProcessLookupError:break
    time.sleep(10)
PY
python run_teacher_apps.py --build data/teacher-apps-v1 --flows data/teacher-app-flows-v1 --core data/teacher-app-core-v1 --bridge data/probe-tools-v1/draper-llvm > state/teacher-apps-v1.log 2>&1

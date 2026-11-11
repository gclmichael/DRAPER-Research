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
report=Path('data/core-risk-v4/timing/summary.json')
for _ in range(180):
    if report.exists():
        try:
            if json.loads(report.read_text())['status']=='complete':break
        except json.JSONDecodeError:pass
    time.sleep(5)
else:raise SystemExit('Previous timing did not complete within 15 minutes')
PY
python -m unittest discover -s tests -v > state/core-final-tests-32.log 2>&1
python run_core_risk.py data/large-flow-v2 --output data/large-core-v2 --fresh-calibration > state/large-core-v2.log 2>&1
python measure_runtime.py data/large-core-v2 --variants calibrated risk-projected --rounds 15 --seconds 0.3 > state/large-core-v2-timing.log 2>&1

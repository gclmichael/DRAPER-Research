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
while True:
    try:
        if json.loads(p.read_text())['status']=='finished':break
    except (FileNotFoundError,json.JSONDecodeError):pass
    time.sleep(10)
PY
python remeasure_all.py data/all-flows-v2 data/all-core-v2 > state/remeasure-v1.log 2>&1
bash run_followon_suites.sh > state/followon-suites-v2.log 2>&1
python run_teacher_apps.py --build data/teacher-apps-v1 --flows data/teacher-app-flows-v1 --core data/teacher-app-core-v1 --bridge data/probe-tools-v1/draper-llvm > state/teacher-apps-v1.log 2>&1
python - <<'PY'
import json
from pathlib import Path
from run_draper import save
out=Path('data/all-flows-v3');out.mkdir()
records={}
for name in ('all-flows-v2','nonlocal-flows-v1'):
    root=Path('data')/name
    for row in json.loads((root/'summary.json').read_text())['programs']:
        if row['program'] not in records or row['status']=='complete':records[row['program']]=(row,root)
for name,(row,root) in records.items():
    if row['status']=='complete':(out/name).symlink_to((root/name).resolve(),target_is_directory=True)
save(out/'summary.json',{'status':'finished','total':len(records),'programs':[v[0] for v in records.values()]})
PY
python run_all_core.py data/all-flows-v3 --output data/all-transformed-core-v1 --bridge data/probe-tools-v1/draper-llvm --transformed > state/all-transformed-core-v1.log 2>&1
python summarize_public_results.py --output data/public-results-v1 > state/public-summary.log 2>&1

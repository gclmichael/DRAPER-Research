#!/usr/bin/env bash
set -euo pipefail
cd /data/chaoni/chengli/draper-node3
export PATH=/data/chaoni/chengli/tools/draper-env/bin:$PATH
export LD_LIBRARY_PATH=/data/chaoni/chengli/tools/draper-env/lib
export TMPDIR=/data/chaoni/chengli/draper-node3/tmp
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
python -m unittest discover -s tests -v > state/core-final-tests.log 2>&1
python index_large_corpus.py data/industrial-corpus-v1 --name paired-records-v2 > state/industrial-corpus-index-v2.log 2>&1
python prepare_large_experiment.py data/industrial-corpus-v1 --bridge data/core-risk-v3/draper-llvm --output data/large-flow-v2 > state/large-flow-v2.log 2>&1
python run_core_risk.py data/large-flow-v2 --output data/large-core-v1 --fresh-calibration > state/large-core-v1.log 2>&1
python measure_runtime.py data/large-core-v1 --variants calibrated risk-projected --rounds 15 --seconds 0.3 > state/large-core-v1-timing.log 2>&1
python run_core_risk.py data/node3-seed42-v1 --output data/core-risk-v4 --fresh-calibration > state/core-risk-v4.log 2>&1
python measure_runtime.py data/core-risk-v4 --variants calibrated risk-projected --rounds 15 --seconds 0.3 > state/core-risk-v4-timing.log 2>&1

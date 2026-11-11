#!/usr/bin/env bash
set -euo pipefail
cd /data/chaoni/chengli/draper-node3
export PATH=/data/chaoni/chengli/tools/draper-env/bin:$PATH
export LD_LIBRARY_PATH=/data/chaoni/chengli/tools/draper-env/lib:${LD_LIBRARY_PATH:-}
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export TMPDIR=/data/chaoni/chengli/draper-node3/tmp
mkdir -p "$TMPDIR" state
exec 9>state/experiment.lock
flock -n 9 || { echo 'An experiment is already running'; exit 1; }
trap 'result=$?; echo "$result" > state/last-exit-code' EXIT
{
    date -Is
    uname -a
    clang --version
    llvm-config --version
    python --version
    python -m pip freeze
} > state/node3-environment.txt 2>&1
python -m unittest discover -s tests -v > state/node3-tests.log 2>&1
python run_draper.py --programs crc32 slre qrduino nettle-aes picojpeg wikisort \
    --rate 0.015625 --missing 0.15 --bootstrap 20 --anchors 3 --seed 42 \
    --selection-controls --output data/node3-seed42-v1
python analyze_remarks.py data/node3-seed42-v1
python measure_runtime.py data/node3-seed42-v1 --rounds 9 --seconds 0.2

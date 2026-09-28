#!/bin/sh
# One bounded smoke run, not a recurring scheduler. No external credentials.
set -eu
cd "${AQUANT_HOME:-/root/aquant}"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
RUN_DIR="${AQUANT_RUN_DIR:-/root/autodl-tmp/aquant/runs/ten-am-smoke}"
mkdir -p "$RUN_DIR"
"${PYTHON:-python3}" -m unittest discover -s tests -v > "$RUN_DIR/tests.log" 2>&1
"${PYTHON:-python3}" -m aquant demo --root "$RUN_DIR/demo" --sessions 300 > "$RUN_DIR/demo.log" 2>&1
# max-seconds is checked cooperatively; timeout adds a process-level ceiling.
timeout 360 "${PYTHON:-python3}" -m aquant mine \
  --config configs/demo.json --data "$RUN_DIR/demo/data" --asof 2026-02-25 \
  --out "$RUN_DIR/mining.json" --candidates 48 --shortlist 8 --max-seconds 300 > "$RUN_DIR/mining.log" 2>&1
"${PYTHON:-python3}" -m aquant health --state "$RUN_DIR/mining.status.json" --expected-date 2026-02-25

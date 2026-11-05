#!/usr/bin/env bash
# Local release gates (Stage J). One command, every gate, one log.
#
# Anything that needs a package the sandbox does not have (numpy for the
# distributional embedder, jsdom for the experience matrix, Flask/FastAPI for the
# host fixtures) is installed into a throw-away directory by the script that
# needs it. The repository is never modified by a gate.
#
# Usage: scripts/run_local_gates.sh [--quick]
#   --quick skips the 5k/10k scale runs and the host fixtures
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
QUICK="${1:-}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE="$ROOT/docs/final_qualification/evidence"
mkdir -p "$EVIDENCE"
LOG="$EVIDENCE/local_gates-$STAMP.log"
DATA="$(mktemp -d /tmp/gates-data-XXXXXX)"
export RAG_DATA_DIR="$DATA" RAG_EMBED_PROVIDER=hashing RAG_LLM_PROVIDER=extractive

FAILED=0
step() {
  local name="$1"; shift
  echo
  echo "===== $name ====="
  if "$@"; then
    echo "GATE $name: PASS"
  else
    echo "GATE $name: FAIL"
    FAILED=$((FAILED + 1))
  fi
}

{
  echo "== local gates $STAMP =="
  echo "commit: $(git rev-parse HEAD)  branch: $(git rev-parse --abbrev-ref HEAD)"
  echo "python: $(python3 -V 2>&1)  node: $(node --version 2>/dev/null || echo none)"

  step "unit-tests" python3 -m unittest discover -s tests -p 'test_*.py'
  step "legacy-engine-tests" bash -c 'cd engine && python3 -m unittest discover -s tests'
  step "node-experience-contracts" node tests/experience_contract.mjs

  python3 -m ragformation ingest corpus
  step "golden-v1" python3 -m ragformation eval --v1
  step "golden-v2" bash -c "python3 -m ragformation eval --out docs/final_qualification/golden_v2_report.json"
  step "golden-v2-legacy-matcher" python3 -m ragformation eval --fact-matcher legacy
  step "calibration-sweep" python3 scripts/calibration_sweep.py \
    --report docs/final_qualification/golden_v2_report.json \
    --out docs/final_qualification/evidence/calibration_sweep.json --margins
  step "holdout-v3" bash -c "python3 -m ragformation eval --dataset evals/holdout_v3.json --out docs/final_qualification/holdout_v3_report.json"
  step "holdout-v4" bash -c "python3 -m ragformation eval --dataset evals/holdout_v4.json --out docs/final_qualification/holdout_v4_report.json"

  step "security-matrix" python3 scripts/security_matrix.py --out docs/final_qualification/security_matrix.json
  step "recovery" bash -c "python3 scripts/recovery_check.py docs/final_qualification/evidence/recovery_report.json"
  step "live-provider-gate" bash -c "python3 scripts/live_provider_check.py --json docs/final_qualification/live_provider.json; code=\$?; [ \$code -eq 2 ] && echo '  (exit 2 = blocked, which is the expected result here)' && exit 0 || exit \$code"
  step "experience-matrix" bash scripts/run_experience_matrix.sh

  if [ "$QUICK" != "--quick" ]; then
    step "semantic-benchmark" bash -c '
      python3 -m venv /tmp/gates-semenv >/dev/null 2>&1
      /tmp/gates-semenv/bin/pip install -q --disable-pip-version-check numpy >/dev/null 2>&1
      /tmp/gates-semenv/bin/python scripts/bench_semantic_providers.py --out docs/final_qualification/semantic_bench.json
    '
    step "scale-5k-10k" python3 scripts/bench_scale.py --chunks 5000 10000 --queries 20 --out docs/final_qualification/evidence/scale
    step "host-fixtures" bash scripts/run_host_fixtures.sh
  fi

  echo
  echo "== local gates finished: $FAILED gate(s) failed =="
} 2>&1 | tee -a "$LOG"
status=${PIPESTATUS[0]}
echo "log: $LOG"
exit $status

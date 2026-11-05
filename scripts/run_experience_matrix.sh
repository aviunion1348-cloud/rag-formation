#!/usr/bin/env bash
# Experience renderer + accessibility matrix (Stage H).
#
# jsdom is installed into a scratch directory (never into the repository) because
# ES module resolution looks next to the importing file. The script is copied
# there and points back at the checkout through RAG_ROOT.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="$ROOT/docs/final_qualification/evidence"
mkdir -p "$EVIDENCE_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$EVIDENCE_DIR/experience-$STAMP.log"
WORK="$(mktemp -d /tmp/experience-XXXXXX)"

{
  echo "== experience matrix $STAMP =="
  echo "node: $(node --version)   jsdom: installing into $WORK (not committed)"
  cd "$WORK" || exit 1
  npm init -y >/dev/null 2>&1
  npm install --silent jsdom >/dev/null 2>&1 || { echo "npm install jsdom failed (network?)"; exit 1; }
  echo "jsdom: $(node -e "console.log(require('jsdom/package.json').version)" 2>/dev/null || echo unknown)"
  cp "$ROOT/scripts/experience_matrix.mjs" "$WORK/experience_matrix.mjs"
  RAG_ROOT="$ROOT" node "$WORK/experience_matrix.mjs"
  code=$?
  echo "exit: $code"
  exit $code
} 2>&1 | tee -a "$LOG"
status=${PIPESTATUS[0]}
rm -rf "$WORK"
exit $status

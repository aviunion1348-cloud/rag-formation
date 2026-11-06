#!/usr/bin/env bash
# Record the pre-registered scenarios with asciinema (real timing, no idle-time limit).
#
#   scripts/session_recordings/record_all.sh <run-id> <stamp> [scenario ids ...]
#
# Each scenario is one `asciinema rec` session whose only command is the harness.
# script(1) provides the pseudo-terminal asciinema needs when no controlling TTY
# exists (CI, sandboxes). An existing cast is never overwritten: a re-run must use
# a new run id, so earlier attempts stay visible.
set -u
cd "$(dirname "$0")/../.."
RUN_ID="$1"; STAMP="$2"; shift 2
ROOT="/tmp/${RUN_ID}"
PKG="artifacts/agent_session_recordings"
PLAN="scripts/session_recordings/scenario_plan.json"
ASCIINEMA="/tmp/recvenv/bin/asciinema"
mkdir -p "${PKG}/terminal" "${PKG}/evidence" "${ROOT}"
IDS="${*:-01 02 03 04 05 06 07 08 09}"
for NN in ${IDS}; do
  FILE=$(python3 -c "import json; p=json.load(open('${PLAN}')); print(next(s['file'] for s in p['scenarios'] if s['id']=='${NN}'))")
  CAST="${PKG}/terminal/${FILE}.cast"
  if [ -e "${CAST}" ]; then
    echo "refusing to overwrite ${CAST}; use a new run id" >&2
    exit 3
  fi
  INNER="python3 scripts/session_recordings/harness.py --scenario ${NN} --run-id ${RUN_ID} --stamp ${STAMP} --root ${ROOT} --pkg ${PKG}"
  STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  TERM=xterm-256color script -qec "${ASCIINEMA} rec -q --cols 158 --rows 40 -t '${RUN_ID} scenario ${NN}' -c '${INNER}' ${CAST}" /dev/null > "${ROOT}/outer_${NN}.log" 2>&1
  CODE=$?
  echo "{\"scenario\": \"${NN}\", \"cast\": \"${CAST}\", \"recorderStartedAt\": \"${STARTED}\", \"recorderEndedAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"recorderExit\": ${CODE}}" | tee -a "${PKG}/evidence/recorder_runs.jsonl"
done

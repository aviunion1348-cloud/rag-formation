#!/usr/bin/env bash
# Render every scenario cast (and preserved failed attempts) to MP4 with identical encoder settings.
#   scripts/session_recordings/render_all.sh <run-id>
set -eu
cd "$(dirname "$0")/../.."
RUN_ID="$1"
PKG="artifacts/agent_session_recordings"
PY="/tmp/recvenv/bin/python"
R="scripts/session_recordings/render_cast.py"
mkdir -p "${PKG}/evidence/render"
render() {  # cast mp4 label source report
  "${PY}" "${R}" "$1" "$2" --label "$3" --source "$4" --keyframes "/tmp/${RUN_ID}/keyframes/$(basename "$2" .mp4)" > "$5"
  cat "$5" | head -c 300; echo
}
for CAST in "${PKG}"/terminal/*.cast; do
  NAME=$(basename "${CAST}" .cast); NN=${NAME:0:2}; SLUG=$(python3 -c "import json; p=json.load(open('scripts/session_recordings/scenario_plan.json')); print(next(s['name'] for s in p['scenarios'] if s['id']=='${NN}'))")
  render "${CAST}" "${PKG}/${NAME}.mp4" "Lear RAG agent · ${RUN_ID} · S${NN} ${SLUG}" "terminal/${NAME}.cast" "${PKG}/evidence/render/${NAME}.json"
done
for CAST in "${PKG}"/evidence/attempts/*.cast; do
  [ -e "${CAST}" ] || continue
  NAME=$(basename "${CAST}" .cast)
  render "${CAST}" "${PKG}/evidence/attempts/${NAME}.mp4" "Lear RAG agent · ${RUN_ID} · ${NAME} (aborted attempt, kept)" "evidence/attempts/${NAME}.cast" "${PKG}/evidence/render/${NAME}.json"
done

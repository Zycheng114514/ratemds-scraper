#!/usr/bin/env bash
# runloop.sh -- run the scraper repeatedly until the queue drains.
#
# Each pass re-tries any doctors a previous pass left pending (e.g. a transient
# timeout), so the retry pass is AUTOMATIC. A nonzero exit is never convergence,
# even when no doctors were processed. Stop successfully only on exit 0.
#
# Usage:  runloop.sh "<states>" [run-all|scrape-doctors]
# Env: WORKERS(4) MAXPASS(20) TRANSPORT(curl) TARGET(desktop) RETRY_DELAY(60)
#      OUTPUT(./output) LOG(<output>/run.log)
set -uo pipefail

STATES="${1:?usage: runloop.sh \"<states>\" [run-all|scrape-doctors]}"
CMD="${2:-run-all}"
WORKERS="${WORKERS:-4}"
MAXPASS="${MAXPASS:-20}"
TARGET="${TARGET:-desktop}"
TRANSPORT="${TRANSPORT:-curl}"
RETRY_DELAY="${RETRY_DELAY:-60}"
OUTPUT="${OUTPUT:-$PWD/output}"
LOG="${LOG:-$OUTPUT/run.log}"

mkdir -p "$OUTPUT"
[[ "$MAXPASS" =~ ^[1-9][0-9]*$ ]] || { echo 'MAXPASS must be positive'; exit 2; }

# xvfb-run keeps the (headed) Cloudflare-harvest browser off any real display.
# Drop it if you have a real X server and want to watch.
runner=(python)
if [ "$TRANSPORT" = browser ] && command -v xvfb-run >/dev/null 2>&1; then
  runner=(xvfb-run -a python)
fi

rc=1
for i in $(seq 1 "$MAXPASS"); do
  echo "=== runloop pass $i/$MAXPASS $(date -u +%FT%TZ) cmd=$CMD states=$STATES workers=$WORKERS ===" >> "$LOG"
  RATEMDS_OUTPUT_DIR="$OUTPUT" "${runner[@]}" -m ratemds_scraper "$CMD" \
      --states "$STATES" --workers "$WORKERS" --target "$TARGET" \
      --transport "$TRANSPORT" >> "$LOG" 2>&1
  rc=$?
  echo "=== runloop pass $i exit=$rc ===" >> "$LOG"
  if [ "$rc" -eq 0 ]; then echo '=== runloop COMPLETE ===' >> "$LOG"; break; fi
  echo '=== runloop INCOMPLETE; unfinished work remains retryable ===' >> "$LOG"
  [ "$i" -lt "$MAXPASS" ] && sleep "$RETRY_DELAY"
done
echo "=== runloop EXIT code=$rc $(date -u +%FT%TZ) ===" >> "$LOG"
exit "$rc"

#!/usr/bin/env bash
# runloop.sh -- run the scraper repeatedly until the queue drains.
#
# Each pass re-tries any doctors a previous pass left pending (e.g. a transient
# Cloudflare give-up / timeout), so the retry pass is AUTOMATIC. The loop stops
# when a pass processes 0 new doctors (drained, or only permanent failures left)
# or after MAXPASS passes.
#
# Usage:  runloop.sh "<states>" [run-all|scrape-doctors]
# Env:    WORKERS(4) MAXPASS(20) TARGET(desktop) OUTPUT(./output) LOG(<output>/run.log)
set -uo pipefail

STATES="${1:?usage: runloop.sh \"<states>\" [run-all|scrape-doctors]}"
CMD="${2:-run-all}"
WORKERS="${WORKERS:-4}"
MAXPASS="${MAXPASS:-20}"
TARGET="${TARGET:-desktop}"
OUTPUT="${OUTPUT:-$PWD/output}"
LOG="${LOG:-$OUTPUT/run.log}"

mkdir -p "$OUTPUT"

# xvfb-run keeps the (headed) Cloudflare-harvest browser off any real display.
# Drop it if you have a real X server and want to watch.
runner="xvfb-run -a"
command -v xvfb-run >/dev/null 2>&1 || runner=""

last=""
for i in $(seq 1 "$MAXPASS"); do
  echo "=== runloop pass $i/$MAXPASS $(date -u +%FT%TZ) cmd=$CMD states=$STATES workers=$WORKERS ===" >> "$LOG"
  RATEMDS_OUTPUT_DIR="$OUTPUT" $runner python -m ratemds_scraper "$CMD" \
      --states "$STATES" --workers "$WORKERS" --target "$TARGET" >> "$LOG" 2>&1
  n=$(grep "scrape-doctors done:" "$LOG" | tail -1 | grep -oE 'done: [0-9]+' | grep -oE '[0-9]+')
  n="${n:-0}"
  echo "=== runloop pass $i processed=$n ===" >> "$LOG"
  if [ "$n" -eq 0 ];     then echo "=== runloop CONVERGED (0 processed) ===" >> "$LOG"; break; fi
  if [ "$n" = "$last" ]; then echo "=== runloop STALLED (processed=$n unchanged) ===" >> "$LOG"; break; fi
  last="$n"
done
echo "=== runloop EXIT $(date -u +%FT%TZ) ===" >> "$LOG"

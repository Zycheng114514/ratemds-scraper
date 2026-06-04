#!/usr/bin/env bash
# worker.sh -- control the scraper on ONE machine (detached + resumable).
#
# Usage:
#   ./worker.sh start "ca,tx,fl"   # launch the run loop for these states
#   ./worker.sh status             # is it running? progress
#   ./worker.sh tail               # follow the log
#   ./worker.sh stop               # stop (resumable; restart with the same states)
#
# Env:
#   RATEMDS_ROOT   working dir for this worker   (default: ~/ratemds-run)
#   WORKERS        worker threads                (default: 4)
#   RATEMDS_CMD    run-all | scrape-doctors      (default: run-all)
set -uo pipefail

ROOT="${RATEMDS_ROOT:-$HOME/ratemds-run}"
OUTPUT="$ROOT/output"
LOG="$ROOT/run.log"
PIDFILE="$ROOT/run.pgid"
WORKERS="${WORKERS:-4}"
CMD="${RATEMDS_CMD:-run-all}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$ROOT" "$OUTPUT"

running() {
  [ -f "$PIDFILE" ] || return 1
  local g; g="$(cat "$PIDFILE" 2>/dev/null)"
  [ -n "$g" ] && kill -0 "-$g" 2>/dev/null
}

case "${1:-status}" in
  start)
    STATES="${2:-}"
    [ -z "$STATES" ] && { echo "ERROR: need a states list, e.g. start \"ca,tx,fl\""; exit 2; }
    running && { echo "already running (pgid $(cat "$PIDFILE")); 'stop' first"; exit 1; }
    echo "$STATES" > "$ROOT/states"
    WORKERS="$WORKERS" OUTPUT="$OUTPUT" LOG="$LOG" \
      setsid bash "$HERE/runloop.sh" "$STATES" "$CMD" >> "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    sleep 1
    echo "started (pgid $(cat "$PIDFILE")), states=$STATES, workers=$WORKERS"
    echo "log: $LOG"
    ;;
  stop)
    running || { echo "not running"; rm -f "$PIDFILE"; exit 0; }
    g="$(cat "$PIDFILE")"
    kill -TERM "-$g" 2>/dev/null; sleep 3; kill -KILL "-$g" 2>/dev/null
    rm -f "$PIDFILE"
    echo "stopped (progress saved; re-run 'start' to resume)"
    ;;
  status)
    if running; then echo "RUNNING (pgid $(cat "$PIDFILE")), states=$(cat "$ROOT/states" 2>/dev/null)";
    else echo "stopped"; fi
    [ -f "$OUTPUT/doctors.csv" ] && echo "scraped doctors: $(($(wc -l < "$OUTPUT/doctors.csv") - 1))"
    [ -f "$OUTPUT/state/anomalies.log" ] && echo "anomalies: $(wc -l < "$OUTPUT/state/anomalies.log")"
    ;;
  tail)
    tail -f "$LOG"
    ;;
  *)
    echo "usage: worker.sh start \"<states>\" | stop | status | tail"
    ;;
esac

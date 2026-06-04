#!/usr/bin/env bash
# combine.sh -- pull every worker's output and merge into one deduped dataset.
#
# Run on the control machine. Safe to run anytime, as often as you like -- it
# re-pulls the latest CSVs and re-merges, so you can snapshot progress mid-run.
# It does NOT stop the workers.
#
# Usage:
#   ./combine.sh                  # pull all hosts in fleet.conf -> output/combined/
#   LOCAL_OUT=./output ./combine.sh   # also fold in a local worker's output
#
# Env:
#   FLEET_CONF   fleet config         (default: ./fleet.conf next to this script)
#   REMOTE_DIR   worker dir per host  (default: ratemds-run)
#   OUT          combined output dir  (default: ./output/combined)
#   LOCAL_OUT    optional local worker output dir to include as 'local'
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${FLEET_CONF:-$HERE/fleet.conf}"
REMOTE_DIR="${REMOTE_DIR:-ratemds-run}"
OUT="${OUT:-$PWD/output/combined}"
LOCAL_OUT="${LOCAL_OUT:-}"

[ -f "$CONF" ] || { echo "no fleet config at $CONF"; exit 1; }
mkdir -p "$OUT/shards"

echo "==> Pulling worker outputs"
grep -vE '^[[:space:]]*(#|$)' "$CONF" | while read -r h states; do
  echo "  - $h"
  mkdir -p "$OUT/shards/$h"
  rsync -az --ignore-missing-args \
        "$h:$REMOTE_DIR/output/doctors.csv" \
        "$h:$REMOTE_DIR/output/reviews.csv" \
        "$h:$REMOTE_DIR/output/state/anomalies.log" \
        "$OUT/shards/$h/" 2>/dev/null
done

# Optional local worker (output lives on THIS machine, not over ssh).
if [ -n "$LOCAL_OUT" ] && [ -f "$LOCAL_OUT/doctors.csv" ]; then
  echo "  - local"
  mkdir -p "$OUT/shards/local"
  cp -f "$LOCAL_OUT/doctors.csv"         "$OUT/shards/local/" 2>/dev/null
  cp -f "$LOCAL_OUT/reviews.csv"         "$OUT/shards/local/" 2>/dev/null
  cp -f "$LOCAL_OUT/state/anomalies.log" "$OUT/shards/local/" 2>/dev/null
fi

echo "==> Merging + deduplicating"
python3 -m ratemds_scraper.fleet.combine "$OUT"

echo "==> Done."
echo "    doctors: $OUT/doctors.csv"
echo "    reviews: $OUT/reviews.csv"

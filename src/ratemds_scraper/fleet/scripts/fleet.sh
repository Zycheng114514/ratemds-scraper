#!/usr/bin/env bash
# fleet.sh -- drive a fleet of scraper workers over SSH from one control machine.
#
# Reads a fleet config (default: ./fleet.conf) with one worker per line:
#     <ssh-host>   <comma-separated state slugs this worker owns>
# Lines starting with '#' and blank lines are ignored. Give each worker a
# DISJOINT set of states so no work is duplicated (overlap is still safe -- the
# combine step dedupes -- just wasteful).
#
# Usage:
#   ./fleet.sh deploy     # copy worker scripts + install the package on each host
#   ./fleet.sh start      # start every worker on its assigned states
#   ./fleet.sh status     # progress on every worker
#   ./fleet.sh stop       # stop every worker (resumable)
#   ./fleet.sh tail [host] # follow one worker's log (default: first host)
#
# Env:
#   FLEET_CONF   path to the config        (default: ./fleet.conf next to this script)
#   REMOTE_DIR   worker dir on each host   (default: ratemds-run, under $HOME)
#   PIP_SPEC     what to pip-install       (default: ratemds-scraper)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${FLEET_CONF:-$HERE/fleet.conf}"
REMOTE_DIR="${REMOTE_DIR:-ratemds-run}"
PIP_SPEC="${PIP_SPEC:-ratemds-scraper}"
cmd="${1:-status}"

[ -f "$CONF" ] || { echo "no fleet config at $CONF (copy fleet.conf.example -> fleet.conf)"; exit 1; }
hosts() { grep -vE '^[[:space:]]*(#|$)' "$CONF"; }

case "$cmd" in
  deploy)
    hosts | while read -r h states; do
      echo "== $h =="
      ssh "$h" "mkdir -p ~/$REMOTE_DIR"
      scp -q "$HERE/worker.sh" "$HERE/runloop.sh" "$h:$REMOTE_DIR/"
      ssh "$h" "python3 -m pip install -q --user --upgrade '$PIP_SPEC'" \
        && echo "  installed $PIP_SPEC" || echo "  WARN: pip install failed; install $PIP_SPEC manually"
    done
    ;;
  start)
    hosts | while read -r h states; do
      echo "== $h (states=$states) =="
      ssh "$h" "RATEMDS_ROOT=\$HOME/$REMOTE_DIR bash \$HOME/$REMOTE_DIR/worker.sh start '$states'"
    done
    ;;
  stop)
    hosts | while read -r h states; do
      echo "== $h =="
      ssh "$h" "RATEMDS_ROOT=\$HOME/$REMOTE_DIR bash \$HOME/$REMOTE_DIR/worker.sh stop"
    done
    ;;
  status)
    hosts | while read -r h states; do
      echo "== $h =="
      ssh "$h" "RATEMDS_ROOT=\$HOME/$REMOTE_DIR bash \$HOME/$REMOTE_DIR/worker.sh status"
    done
    ;;
  tail)
    h="${2:-$(hosts | sed -n '1p' | awk '{print $1}')}"
    ssh "$h" "tail -f \$HOME/$REMOTE_DIR/run.log"
    ;;
  *)
    echo "usage: fleet.sh deploy|start|stop|status|tail [host]"
    ;;
esac

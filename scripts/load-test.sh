#!/usr/bin/env bash
# Bounded CPU load helper for tuning the HydroShift coolant fan/pump curve.
# It tails the daemon log while running N CPU-bound Python workers.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.suraj.lianli-hydroshift"
LOG_PATH="${LOG_PATH:-$HOME/Library/Logs/lianli-hydroshift/$LABEL.err}"

logical_cpus() {
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.logicalcpu 2>/dev/null && return
  fi
  getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1
}

DURATION=300
LOGICAL_CPUS="$(logical_cpus)"
if [[ "$LOGICAL_CPUS" -gt 2 ]]; then
  DEFAULT_WORKERS=$((LOGICAL_CPUS - 2))
else
  DEFAULT_WORKERS="$LOGICAL_CPUS"
fi
WORKERS="$DEFAULT_WORKERS"
TAIL_LINES=20
TAIL_LOG=1

usage() {
  cat <<EOF
Usage: $0 [options]

Run a bounded CPU load while tailing the lianli-hydroshift daemon log.

Options:
  -d, --duration SECONDS   Duration to run load test (default: $DURATION)
  -w, --workers N          Number of CPU workers (default: $WORKERS; logical CPUs: $LOGICAL_CPUS)
      --log PATH           Log file to tail (default: $LOG_PATH)
      --tail-lines N       Initial log lines to show (default: $TAIL_LINES)
      --no-tail            Do not tail daemon log
  -h, --help               Show this help

Examples:
  $0
  $0 --duration 600 --workers 8
  $0 --duration 300 --workers $LOGICAL_CPUS   # worst-case all-core saturation
  $0 --duration 180 --no-tail
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--duration)
      DURATION="${2:?missing duration}"
      shift 2
      ;;
    -w|--workers)
      WORKERS="${2:?missing workers}"
      shift 2
      ;;
    --log)
      LOG_PATH="${2:?missing log path}"
      shift 2
      ;;
    --tail-lines)
      TAIL_LINES="${2:?missing tail line count}"
      shift 2
      ;;
    --no-tail)
      TAIL_LOG=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$DURATION" in (*[!0-9]*|"") echo "duration must be a positive integer" >&2; exit 2;; esac
case "$WORKERS" in (*[!0-9]*|"") echo "workers must be a positive integer" >&2; exit 2;; esac
case "$TAIL_LINES" in (*[!0-9]*|"") echo "tail-lines must be a non-negative integer" >&2; exit 2;; esac
if [[ "$DURATION" -lt 1 || "$WORKERS" -lt 1 ]]; then
  echo "duration and workers must be >= 1" >&2
  exit 2
fi

burn_pids=()
tail_pid=""
progress_pid=""

cleanup() {
  local status=$?
  if [[ -n "$tail_pid" ]] && kill -0 "$tail_pid" 2>/dev/null; then
    kill "$tail_pid" 2>/dev/null || true
  fi
  if [[ -n "$progress_pid" ]] && kill -0 "$progress_pid" 2>/dev/null; then
    kill "$progress_pid" 2>/dev/null || true
  fi
  if [[ ${#burn_pids[@]} -gt 0 ]]; then
    kill "${burn_pids[@]}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  if [[ $status -eq 130 ]]; then
    echo "Interrupted; stopped load workers."
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

cd "$PROJECT_DIR"

echo "== lianli-hydroshift load test =="
echo "Duration: ${DURATION}s"
echo "Workers:  $WORKERS"
echo "Log:      $LOG_PATH"
echo

if launchctl print "system/$LABEL" >/dev/null 2>&1; then
  echo "LaunchDaemon state:"
  launchctl print "system/$LABEL" | grep -E 'state =|pid =|runs =' || true
  echo
else
  echo "Warning: system/$LABEL is not loaded or not visible to launchctl."
  echo
fi

if [[ "$TAIL_LOG" -eq 1 ]]; then
  mkdir -p "$(dirname "$LOG_PATH")" 2>/dev/null || true
  echo "== daemon log tail =="
  if [[ -r "$LOG_PATH" ]]; then
    tail -n "$TAIL_LINES" -F "$LOG_PATH" &
    tail_pid=$!
  else
    echo "Warning: log is not readable yet: $LOG_PATH"
    echo "Continuing load test without live log tail."
  fi
  sleep 1
  echo
fi

echo "== starting CPU load; press Ctrl+C to stop early =="
BURN_CODE='import math, os, time
end = time.monotonic() + float(os.environ["DURATION"])
x = 0.123456789
while time.monotonic() < end:
    for i in range(20000):
        x = math.sin(x + i) * math.cos(x) + math.sqrt(abs(x) + 1.0)
print(f"worker {os.getpid()} done", flush=True)
'

for _ in $(seq 1 "$WORKERS"); do
  DURATION="$DURATION" python3 -c "$BURN_CODE" &
  burn_pids+=("$!")
done

start_ts=$(date +%s)
(
  while true; do
    sleep 10
    now=$(date +%s)
    elapsed=$((now - start_ts))
    remaining=$((DURATION - elapsed))
    if [[ "$remaining" -lt 0 ]]; then remaining=0; fi
    active=0
    for pid in "${burn_pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        active=$((active + 1))
      fi
    done
    printf 'Load running: elapsed=%ss remaining~=%ss active_workers~=%s\n' "$elapsed" "$remaining" "$active"
  done
) &
progress_pid=$!

# Propagate failures if any worker exited non-zero.
worker_status=0
for pid in "${burn_pids[@]}"; do
  wait "$pid" || worker_status=$?
done

if [[ -n "$progress_pid" ]] && kill -0 "$progress_pid" 2>/dev/null; then
  kill "$progress_pid" 2>/dev/null || true
  progress_pid=""
fi

echo "== load test complete =="
if [[ "$TAIL_LOG" -eq 1 ]]; then
  echo "Leave this running longer with: tail -f '$LOG_PATH'"
fi
exit "$worker_status"

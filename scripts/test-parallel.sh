#!/bin/sh
# Run the whole suite in N shards at once (each test already gets its own database). Usage: scripts/test-parallel.sh [N]
N=${1:-6}; out=$(mktemp -d)
for i in $(seq 0 $((N - 1))); do
  PH_SHARD=$i/$N .venv/bin/python -m pytest -q -p no:cacheprovider >"$out/$i.log" 2>&1 &
done
wait
grep -h -E "^(FAILED|ERROR)" "$out"/*.log
grep -h -E "passed|failed|error" "$out"/*.log | grep -E "^[0-9=]" | tail -n "$N"

#!/bin/sh
# Run the suite in N shards at once. Usage: scripts/test-parallel.sh [N] [pytest arguments, e.g. tests/test_web.py]
# Each shard gets a MongoDB of its own (a throwaway in-memory container, removed afterwards), so shards never
# contend for one server's locks; every test still gets a database of its own inside it.
# PH_TEST_MONGO_BASE: the first shard's port (default 27120; shard i uses BASE+i).
set -u
N=${1:-6}; [ $# -gt 0 ] && shift; BASE=${PH_TEST_MONGO_BASE:-27120}; out=$(mktemp -d); tag=$$
names=""
cleanup() { [ -n "$names" ] && docker rm -f $names >/dev/null 2>&1; }
trap cleanup EXIT INT TERM
for i in $(seq 0 $((N - 1))); do
  name="ph-test-mongo-$tag-$i"; names="$names $name"
  PH_TEST_MONGO=$name PH_TEST_MONGO_PORT=$((BASE + i)) scripts/test-mongo.sh >"$out/mongo-$i.log" 2>&1 &
done
wait
if grep -l "did not start\|Error\|error" "$out"/mongo-*.log >/dev/null 2>&1; then
  cat "$out"/mongo-*.log >&2; echo "A shard's MongoDB did not start" >&2; exit 1
fi
for i in $(seq 0 $((N - 1))); do
  PH_SHARD=$i/$N PRACTICEHUB_TEST_DATABASE="mongodb://127.0.0.1:$((BASE + i))/?replicaSet=rs0&directConnection=true" \
    .venv/bin/python -m pytest -q -p no:cacheprovider "$@" >"$out/$i.log" 2>&1 &
done
wait
grep -h -E "^(FAILED|ERROR)" "$out"/*.log
grep -h -E "passed|failed|error" "$out"/[0-9]*.log | grep -E "^[0-9=]" | tail -n "$N"

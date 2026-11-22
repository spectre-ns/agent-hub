#!/bin/sh
# Run the suite in N shards at once. Usage: scripts/test-parallel.sh [N|auto] [pytest arguments, e.g. tests/test_web.py]
# auto (the default): one shard per core, fewer if free memory allows less (about 700 MB a shard), at most 24.
# Each shard gets a MongoDB of its own (a throwaway in-memory container, removed afterwards), so shards never
# contend for one server's locks; every test still gets a database of its own inside it.
# Each shard's server is on a free port docker picks, so several runs at once never collide.
set -u
N=${1:-auto}; [ $# -gt 0 ] && shift
if [ "$N" = auto ]; then
  mem=$(awk '/MemAvailable/ {print int($2 / 1024 / 700)}' /proc/meminfo); cpu=$(nproc)
  N=$(( cpu < mem ? cpu : mem )); [ "$N" -gt 24 ] && N=24; [ "$N" -lt 1 ] && N=1
fi
echo "$N shards" >&2; out=$(mktemp -d); tag=$$
names=""
cleanup() { [ -n "$names" ] && docker rm -f $names >/dev/null 2>&1; }
trap cleanup EXIT INT TERM
for i in $(seq 0 $((N - 1))); do
  name="ph-test-mongo-$tag-$i"; names="$names $name"
  PH_TEST_MONGO=$name PH_TEST_MONGO_PORT=auto scripts/test-mongo.sh >"$out/mongo-$i.log" 2>&1 &
done
wait
if grep -l "did not start\|Error\|error" "$out"/mongo-*.log >/dev/null 2>&1; then
  cat "$out"/mongo-*.log >&2; echo "A shard's MongoDB did not start" >&2; exit 1
fi
for i in $(seq 0 $((N - 1))); do
  port=$(sed -n 's/^MongoDB replica set rs0 on 127.0.0.1:\([0-9]*\)$/\1/p' "$out/mongo-$i.log")
  PH_SHARD=$i/$N PRACTICEHUB_TEST_DATABASE="mongodb://127.0.0.1:$port/?replicaSet=rs0&directConnection=true" \
    .venv/bin/python -m pytest -q -p no:cacheprovider "$@" >"$out/$i.log" 2>&1 &
done
wait
grep -h -E "^(FAILED|ERROR)" "$out"/*.log
grep -h -E "passed|failed|error" "$out"/[0-9]*.log | grep -E "^[0-9=]" | tail -n "$N"

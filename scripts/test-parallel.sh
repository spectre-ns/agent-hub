#!/bin/sh
# Run the suite in N shards at once. Usage: scripts/test-parallel.sh [N|auto] [pytest arguments, e.g. tests/test_web.py]
# auto (the default): one shard per core, fewer if free memory allows less (about 700 MB a shard), at most 24.
# Each shard gets a MongoDB of its own (a throwaway in-memory container, removed afterwards), so shards never
# contend for one server's locks; every test still gets a database of its own inside it.
# Each shard's server is on a free port docker picks, so several runs at once never collide.
set -u
# Several runs at a time share the machine (PH_TEST_SLOTS, default half the cores): more queue here instead of
# starving each other, and each run takes at most its share of cores and of free memory. A run holds its slot's lock on fd 9, which pytest and docker inherit until the run ends.
lock="${XDG_RUNTIME_DIR:-/tmp}/practicehub-tests.lock"
slots=${PH_TEST_SLOTS:-$(( $(nproc) / 2 ))}; [ "$slots" -lt 1 ] && slots=1
if [ -z "${PH_TEST_LOCKED:-}" ]; then
  said=""
  while :; do
    for k in $(seq 1 "$slots"); do
      f=$lock; [ "$k" -gt 1 ] && f=$lock.$k
      exec 9>"$f"
      if flock -n 9; then PH_TEST_LOCKED=1 exec "$0" "$@"; fi
      exec 9>&-
    done
    [ -z "$said" ] && echo "waiting for a free test slot on this machine" >&2 && said=1
    sleep 3
  done
fi
N=${1:-auto}; [ $# -gt 0 ] && shift
if [ "$N" = auto ]; then
  mem=$(awk '/MemAvailable/ {print int($2 / 1024 / 700)}' /proc/meminfo); cpu=$(nproc)
  N=$(( cpu < mem ? cpu : mem )); [ "$N" -gt 24 ] && N=24
fi
# This run's share: the cores over the slots, and what free memory allows now (about 700 MB a shard).
share=$(( $(nproc) / slots )); mem=$(awk '/MemAvailable/ {print int($2 / 1024 / 700)}' /proc/meminfo)
[ "$mem" -lt "$share" ] && share=$mem
[ "$N" -gt "$share" ] && N=$share; [ "$N" -lt 1 ] && N=1
echo "$N shards" >&2; out=$(mktemp -d); tag=$$; start=$(date +%s)
# the hubs the tests start from (a pack installed, its demo...) are built once per run and shared by the shards
mkdir "$out/snapshots"
names=""
cleanup() { [ -n "$names" ] && docker rm -f $names >/dev/null 2>&1; rm -rf "$out/snapshots"; }
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
  addr=127.0.0.1:$port
  # the container's own address, where the host can reach it (Linux): a test makes thousands of round trips, and
  # through docker's published port each one also crosses docker-proxy, about twice as slow
  ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "ph-test-mongo-$tag-$i" 2>/dev/null)
  if [ -n "$ip" ] && .venv/bin/python -c "import socket, sys; socket.create_connection((sys.argv[1], 27017), 1)" "$ip" \
      2>/dev/null; then addr=$ip:27017; fi
  PH_SHARD=$i/$N PH_SNAPSHOTS="$out/snapshots" PRACTICEHUB_TEST_DATABASE="mongodb://$addr/?replicaSet=rs0&directConnection=true" \
    .venv/bin/python -m pytest -q -p no:cacheprovider "$@" >"$out/$i.log" 2>&1 &
done
wait
grep -h -E "^(FAILED|ERROR)" "$out"/*.log
grep -h -E "passed|failed|error" "$out"/[0-9]*.log | grep -E "^[0-9=]" | tail -n "$N"
echo "$(( $(date +%s) - start ))s in all, logs in $out" >&2

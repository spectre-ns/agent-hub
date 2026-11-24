#!/bin/sh
# Run the suite in N shards at once. Usage: scripts/test-parallel.sh [N|auto] [pytest arguments, e.g. tests/test_web.py]
# auto (the default): one shard per core, fewer if free memory allows less (about 700 MB a shard), at most 24.
# The shards (and every other run on the machine) share one MongoDB, ph-test-mongo-shared; every test process
# has databases of its own inside it.
set -u
# Several runs at a time share the machine (PH_TEST_SLOTS, default half the cores): more queue here instead of
# starving each other, and each run takes at most its share of cores and of free memory. A run holds its slot's lock on fd 9, which pytest and docker inherit until the run ends.
lock="${XDG_RUNTIME_DIR:-/tmp}/practicehub-tests.lock"
slots=${PH_TEST_SLOTS:-$(( $(nproc) / 2 ))}; [ "$slots" -lt 1 ] && slots=1
if [ -z "${PH_TEST_LOCKED:-}" ]; then
  said=""
  while :; do
    # any free slot of the machine's (half the cores): PH_TEST_SLOTS sets a run's share of the cores, not which
    # slots it may take, so a whole-machine run never waits on a small one holding the first
    for k in $(seq 1 "$(( $(nproc) / 2 > 1 ? $(nproc) / 2 : 1 ))"); do
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
# One MongoDB serves every run on the machine (tests no longer create collections per test, so shards do not wait on
# each other's catalog locks): it stays up between runs, and each test process drops its own databases when it ends.
# Databases of a process that died (pht<pid>_...) are dropped here.
name=ph-test-mongo-shared
cleanup() { rm -rf "$out/snapshots"; }
trap cleanup EXIT INT TERM
if ! flock "$lock.mongo" env PH_TEST_MONGO=$name PH_TEST_MONGO_PORT=auto PH_TEST_MONGO_CACHE_GB="${PH_TEST_MONGO_CACHE_GB:-2}" \
    scripts/test-mongo.sh >"$out/mongo.log" 2>&1; then
  cat "$out/mongo.log" >&2; echo "The tests' MongoDB did not start" >&2; exit 1
fi
port=$(sed -n 's/^MongoDB replica set rs0 on 127.0.0.1:\([0-9]*\)$/\1/p' "$out/mongo.log")
[ -n "$port" ] || { cat "$out/mongo.log" >&2; echo "The tests' MongoDB gave no port" >&2; exit 1; }
addr=127.0.0.1:$port
# the container's own address, where the host can reach it (Linux): a test makes thousands of round trips, and
# through docker's published port each one also crosses docker-proxy, about twice as slow
ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$name" 2>/dev/null)
if [ -n "$ip" ] && .venv/bin/python -c "import socket, sys; socket.create_connection((sys.argv[1], 27017), 1)" "$ip" \
    2>/dev/null; then addr=$ip:27017; fi
uri="mongodb://$addr/?replicaSet=rs0&directConnection=true"
.venv/bin/python - "$uri" <<'PYEOF'
import os, re, sys
import pymongo
c = pymongo.MongoClient(sys.argv[1], serverSelectionTimeoutMS=5000)
for name in c.list_database_names():
    m = re.match(r"pht(\d+)_", name)
    if m and not os.path.exists(f"/proc/{m.group(1)}"):
        c.drop_database(name)
PYEOF
for i in $(seq 0 $((N - 1))); do
  PH_SHARD=$i/$N PH_SNAPSHOTS="$out/snapshots" PRACTICEHUB_TEST_DATABASE="$uri" \
    .venv/bin/python -m pytest -q -p no:cacheprovider "$@" >"$out/$i.log" 2>&1 &
done
wait
grep -h -E "^(FAILED|ERROR)" "$out"/*.log
grep -h -E "passed|failed|error" "$out"/[0-9]*.log | grep -E "^[0-9=]" | tail -n "$N"
echo "$(( $(date +%s) - start ))s in all, logs in $out" >&2

#!/bin/sh
# Start the MongoDB the tests use: a one-node replica set (transactions need one) on 127.0.0.1:27118.
# Then: PRACTICEHUB_TEST_DATABASE is optional; the tests default to this address.
set -eu
NAME=${PH_TEST_MONGO:-ph-test-mongo}
PORT=${PH_TEST_MONGO_PORT:-27118}   # "auto": a free port docker picks (printed last)
# Data lives on disk (an anonymous volume, removed with the container), not on a tmpfs: tmpfs pages can only be
# swapped, while disk pages are page cache the kernel drops at once when memory is short.
CACHE=${PH_TEST_MONGO_CACHE_GB:-0.25}   # a test server holds little: keep its cache small, so many can run at once
if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  # every test makes its own database (dozens of collections and index files): raise the open-file limit,
  # and keep the data in memory, since it is thrown away
  bind="127.0.0.1:$PORT:27017"; [ "$PORT" = auto ] && bind="127.0.0.1::27017"
  # PH_TEST_MONGO_TMPFS=1: the data in memory instead. Commits then wait on no disk (under WSL the disk is what keeps
  # the shards idle), at the cost of memory the kernel cannot drop. Applies when the container is (re)created.
  mem=""; [ -n "${PH_TEST_MONGO_TMPFS:-}" ] && mem="--tmpfs /data/db"
  # shellcheck disable=SC2086
  docker run -d --name "$NAME" -p "$bind" --ulimit nofile=1048576:1048576 --rm $mem \
    mongo:7.0 --replSet rs0 --bind_ip_all --wiredTigerCacheSizeGB "$CACHE" >/dev/null
fi
PORT=$(docker port "$NAME" 27017/tcp | head -n 1 | sed 's/.*://')
PY=$(dirname "$0")/../.venv/bin/python
if [ -x "$PY" ]; then
  # from the host, every 0.1 s (a mongosh in the container costs a second each time): up, initiated, and primary
  "$PY" - "$PORT" <<'PYEOF' || { echo "MongoDB did not start" >&2; exit 1; }
import sys, time
import pymongo
c = pymongo.MongoClient(f"mongodb://127.0.0.1:{sys.argv[1]}/?directConnection=true", serverSelectionTimeoutMS=500)
end = time.time() + 60
while time.time() < end:
    try:
        hello = c.admin.command("hello")
        if hello.get("isWritablePrimary"):
            sys.exit(0)
        if not hello.get("setName"):
            c.admin.command("replSetInitiate", {"_id": "rs0", "members": [{"_id": 0, "host": "localhost:27017"}]})
    except pymongo.errors.PyMongoError:
        pass
    time.sleep(0.1)
sys.exit(1)
PYEOF
else
  i=0
  until docker exec "$NAME" mongosh --quiet --eval 'db.adminCommand("ping").ok' >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -gt 60 ] && { echo "MongoDB did not start" >&2; exit 1; }; sleep 1
  done
  docker exec "$NAME" mongosh --quiet --eval \
    "try { rs.status().ok } catch (e) { rs.initiate({_id: 'rs0', members: [{_id: 0, host: 'localhost:27017'}]}).ok }" >/dev/null
fi
echo "MongoDB replica set rs0 on 127.0.0.1:$PORT"

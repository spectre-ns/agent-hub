#!/bin/sh
# Start the MongoDB the tests use: a one-node replica set (transactions need one) on 127.0.0.1:27118.
# Then: PRACTICEHUB_TEST_DATABASE is optional; the tests default to this address.
set -eu
NAME=${PH_TEST_MONGO:-ph-test-mongo}
PORT=${PH_TEST_MONGO_PORT:-27118}
if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  # every test makes its own database (dozens of collections and index files): raise the open-file limit,
  # and keep the data in memory, since it is thrown away
  docker run -d --name "$NAME" -p "127.0.0.1:$PORT:27017" --ulimit nofile=1048576:1048576 --tmpfs /data/db \
    mongo:7.0 --replSet rs0 --bind_ip_all >/dev/null
fi
i=0
until docker exec "$NAME" mongosh --quiet --eval 'db.adminCommand("ping").ok' >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "MongoDB did not start" >&2; exit 1; }; sleep 1
done
docker exec "$NAME" mongosh --quiet --eval \
  "try { rs.status().ok } catch (e) { rs.initiate({_id: 'rs0', members: [{_id: 0, host: 'localhost:27017'}]}).ok }" >/dev/null
echo "MongoDB replica set rs0 on 127.0.0.1:$PORT"

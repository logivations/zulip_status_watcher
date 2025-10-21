
docker build -t zulip-status-watcher .

docker run  -v /data:/data \
  --name zulip-watcher \
  --rm \
  zulip-status-watcher

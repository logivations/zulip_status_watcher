#!/bin/bash

docker stop zulip-watcher
docker rm zulip-watcher

docker build -t zulip-status-watcher .

docker run -d \
  --log-opt max-size=50m --log-opt max-file=3 \
  -v /data:/data \
  --add-host w2mo.logivations.com:100.80.191.69 \
  --name zulip-watcher \
  --restart unless-stopped \
  zulip-status-watcher

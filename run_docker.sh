#!/bin/bash

docker build -t zulip-status-watcher .

docker run -d \
  -v /data:/data \
  --name zulip-watcher \
  --restart unless-stopped \
  zulip-status-watcher

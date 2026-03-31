#!/bin/bash
echo "=== GATEWAY ==="
openclaw gateway status 2>&1 | head -5
echo ""
echo "=== CRON JOBS ==="
openclaw cron list 2>&1 | grep -cE "idle|ok|running|error"
echo ""
echo "=== BRIDGE ==="
curl -s http://localhost:8787/health 2>&1
echo ""
curl -s http://localhost:8787/stats 2>&1
echo ""
echo "=== BRIDGE ERRORS (last 6h) ==="
sudo docker logs cloudiqs-bridge --since 6h 2>&1 | grep -iE "error|fail" | tail -10
echo ""
echo "=== S3 POLLER ==="
ls -la ~/s3-upload-poller.py 2>/dev/null && echo "exists" || echo "MISSING"
crontab -l 2>&1 | grep -i poller || echo "NO POLLER CRON"
echo ""
echo "=== DONE ==="

Run a full health check on the live CloudiQS EC2 instance using SSM. Do not SSH — use SSM send-command only.

Execute these checks in order, collecting all output before reporting:

1. Run `bash /home/ubuntu/cloudiqs-engine/scripts/diagnostics.sh` via SSM
2. Run `sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'` via SSM
3. Run `sudo docker logs cloudiqs-bridge --since 24h 2>&1 | grep -cE 'ERROR|CRITICAL' || echo 0` via SSM
4. Run `df -h / && echo '---' && free -h` via SSM
5. Run `openclaw cron list 2>/dev/null | grep -cE 'idle|ok|running|error' || echo 0` via SSM

For each SSM command:
- Send command and capture COMMAND_ID
- Poll status every 5 seconds, up to 30 seconds
- Retrieve StandardOutputContent when done

Then report in this format:

```
## CloudiQS Engine Health — [timestamp]

### Bridge                    ✅ OK / ⚠️ WARNING / ❌ DOWN
[health endpoint result and error count]

### Docker                    ✅ OK / ⚠️ WARNING / ❌ DOWN
[container name, status, uptime]

### OpenClaw Gateway          ✅ OK / ⚠️ WARNING / ❌ DOWN
[cron job count vs expected 46]

### Disk & Memory             ✅ OK / ⚠️ WARNING / ❌ CRITICAL
[disk % used, free memory]

### Errors (last 24h)         ✅ 0 errors / ⚠️ N errors
[top error lines if any]

---
### Action required
[Only if something needs fixing — specific steps]
```

Thresholds:
- Bridge errors: 0-5 OK, 6-20 WARNING, 21+ CRITICAL
- Cron jobs: 44-46 OK, 38-43 WARNING, <38 CRITICAL
- Disk: <70% OK, 70-85% WARNING, >85% CRITICAL
- Free memory: >1GB OK, 500MB-1GB WARNING, <500MB CRITICAL

If SSM fails, say so and suggest checking the AWS console for instance status.

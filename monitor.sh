#!/usr/bin/env bash
set -euo pipefail
cd /home/richie/traffic-monitor/  
UV_BIN="/home/richie/.local/bin/uv"

# Run and append logs
"$UV_BIN" run main.py >> /home/richie/traffic-monitor/cron.log 2>&1


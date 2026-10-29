#!/usr/bin/env bash
set -euo pipefail
cd /home/richie/traffic/  
UV_BIN="/home/richie/.local/bin/uv"

# Run and append logs
export DATA_DIR=/home/richie/traffic/data

"$UV_BIN" run "${1:-main.py}" >>/home/richie/traffic/cron.log 2>&1


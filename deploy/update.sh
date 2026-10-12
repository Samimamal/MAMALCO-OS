#!/usr/bin/env bash
# Pull the latest code and restart:  sudo bash /opt/mamal/deploy/update.sh
set -euo pipefail
cd /opt/mamal
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
systemctl restart mamal
echo "Updated to $(git log -1 --format='%h %s')"

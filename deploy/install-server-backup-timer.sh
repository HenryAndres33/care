#!/usr/bin/env bash
# Install the nightly server backup as a system timer. Run ON THE SERVER:
#   sudo bash deploy/install-server-backup-timer.sh
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[ "$EUID" = 0 ] || { echo "run with sudo" >&2; exit 1; }
install -m 0644 systemd/care-suriname-server-backup.service systemd/care-suriname-server-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now care-suriname-server-backup.timer
systemctl list-timers care-suriname-server-backup.timer --no-pager
echo "Test it now:  sudo systemctl start care-suriname-server-backup.service && journalctl -u care-suriname-server-backup -n 12 --no-pager"

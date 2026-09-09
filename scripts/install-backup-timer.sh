#!/usr/bin/env bash
# Install the nightly CARE Suriname backup timer as a systemd user unit.
#
#   bash scripts/install-backup-timer.sh
#
# Run as the normal desktop user, not with sudo. Re-running updates and
# restarts the same units. See care_fe/docs/backups.md.

set -euo pipefail

care_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
service=care-suriname-backup.service
timer=care-suriname-backup.timer

if [ "$EUID" = 0 ]; then
  echo "Run this as your normal desktop user, without sudo." >&2
  exit 1
fi

for cmd in systemctl loginctl docker; do
  command -v "$cmd" >/dev/null || {
    echo "Required command not found: $cmd" >&2
    exit 1
  }
done

[ -f "$care_dir/scripts/care-suriname-backup.sh" ] || {
  echo "Backup script not found under $care_dir/scripts." >&2
  exit 1
}

systemctl --user show-environment >/dev/null

# Lingering keeps user timers running when nobody is logged in. Without it a
# nightly backup only fires while the desktop session happens to be open.
loginctl enable-linger "$(id -un)"
if [ "$(loginctl show-user "$(id -un)" -p Linger --value)" != yes ]; then
  echo "Could not enable lingering; the timer would not survive logout." >&2
  exit 1
fi

install -d -m 0700 "$unit_dir"
install -m 0644 "$care_dir/scripts/systemd/$service" "$unit_dir/$service"
install -m 0644 "$care_dir/scripts/systemd/$timer" "$unit_dir/$timer"

systemctl --user daemon-reload
systemctl --user enable "$timer"
systemctl --user restart "$timer"

echo "Installed. Next run:"
systemctl --user list-timers "$timer" --no-pager

cat <<EOF

Verify the backup itself works now, rather than discovering it at 02:30:

  systemctl --user start $service
  systemctl --user status $service --no-pager
  ls -la "\${CARE_BACKUP_DIR:-\$HOME/care-suriname-backups}"

To uninstall:

  systemctl --user disable --now $timer
EOF

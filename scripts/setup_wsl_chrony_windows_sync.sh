#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

if ! command -v chronyd >/dev/null 2>&1 || ! command -v chronyc >/dev/null 2>&1; then
    echo "chrony is not installed. Run: sudo apt install -y chrony" >&2
    exit 1
fi

if [ ! -e /dev/ptp_hyperv ]; then
    echo "/dev/ptp_hyperv is unavailable; refusing to configure Windows-host synchronization." >&2
    exit 1
fi

defaults_file=/etc/default/chrony
defaults_tmp=$(mktemp)
source_tmp=$(mktemp)
trap 'rm -f "$defaults_tmp" "$source_tmp"' EXIT HUP INT TERM

awk '
BEGIN { replaced = 0 }
/^[[:space:]]*SYNC_IN_CONTAINER=/ {
    print "SYNC_IN_CONTAINER=\"yes\""
    replaced = 1
    next
}
{ print }
END {
    if (!replaced) print "SYNC_IN_CONTAINER=\"yes\""
}
' "$defaults_file" >"$defaults_tmp"
install -o root -g root -m 0644 "$defaults_tmp" "$defaults_file"

cat >"$source_tmp" <<'EOF'
# Keep WSL on the same clock as its Windows host through Hyper-V PTP.
# Public NTP pools remain available only as comparison sources.
refclock PHC /dev/ptp_hyperv poll 2 dpoll -2 prefer trust
EOF
install -d -o root -g root -m 0755 /etc/chrony/conf.d
install -o root -g root -m 0644 \
    "$source_tmp" /etc/chrony/conf.d/ydtrader-windows-host.conf

systemctl disable --now systemd-timesyncd.service >/dev/null 2>&1 || true
systemctl enable chrony.service >/dev/null
systemctl restart chrony.service

sleep 2
if ps -C chronyd -o args= | grep -Eq '(^|[[:space:]])-[[:alnum:]]*x([[:space:]]|$)'; then
    echo "chronyd still has -x and cannot control the WSL clock." >&2
    systemctl status chrony.service --no-pager -l >&2 || true
    exit 2
fi

chronyc waitsync 30 0.020 0.0 1
chronyc makestep
chronyc waitsync 10 0.020 0.0 1

if ! chronyc -c sources -n | awk -F, '
    $1 == "#" && $2 == "*" && $3 == "PHC0" { found = 1 }
    END { exit found ? 0 : 1 }
'; then
    echo "chrony did not select the Windows Hyper-V PHC0 clock." >&2
    chronyc sources -v >&2
    exit 3
fi

echo "Windows-host synchronization is active."
chronyc tracking
chronyc sources -v

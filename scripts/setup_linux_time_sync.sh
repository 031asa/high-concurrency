#!/bin/sh
set -eu

usage() {
    echo "Usage: sudo $0 --config <time-authority.conf>" >&2
    exit 1
}

[ "$#" -eq 2 ] && [ "$1" = "--config" ] || usage
config=$2
[ "$(id -u)" -eq 0 ] || { echo "Run this script with sudo." >&2; exit 1; }
[ -f "$config" ] || { echo "Config not found: $config" >&2; exit 1; }
command -v chronyd >/dev/null 2>&1 && command -v chronyc >/dev/null 2>&1 || {
    echo "chrony is not installed. On Ubuntu: sudo apt install -y chrony" >&2
    exit 1
}

config_value() {
    awk -F= -v wanted="$1" '
        $1 == wanted {
            sub(/^[^=]*=/, "")
            gsub(/^[[:space:]]+|[[:space:]]+$/, "")
            print
            found = 1
        }
        END { if (!found) exit 1 }
    ' "$config"
}

authority_name=$(config_value authority_name) || { echo "Missing authority_name." >&2; exit 1; }
authority_url=$(config_value authority_url) || { echo "Missing authority_url." >&2; exit 1; }
ntp_servers=$(config_value ntp_servers) || { echo "Missing ntp_servers." >&2; exit 1; }
environment=$(config_value environment) || { echo "Missing environment." >&2; exit 1; }
max_offset_ms=$(config_value max_offset_ms) || { echo "Missing max_offset_ms." >&2; exit 1; }

case "$authority_url" in http://*|https://*) ;; *) echo "authority_url must be HTTP(S)." >&2; exit 1;; esac
case "$environment" in test|production) ;; *) echo "environment must be test or production." >&2; exit 1;; esac
case "$max_offset_ms" in ''|*[!0-9]*) echo "max_offset_ms must be a positive integer." >&2; exit 1;; esac
[ "$max_offset_ms" -gt 0 ] || { echo "max_offset_ms must be positive." >&2; exit 1; }
case "$ntp_servers" in *REPLACE_*|*PLACEHOLDER*) echo "Refusing placeholder NTP configuration." >&2; exit 1;; esac
for server in $ntp_servers; do
    case "$server" in *[!A-Za-z0-9._:-]*|'') echo "Invalid NTP server: $server" >&2; exit 1;; esac
done

managed=/etc/chrony/ydtrader.conf
old_managed=/etc/chrony/conf.d/ydtrader-windows-host.conf
defaults=/etc/default/chrony
tmp_config=$(mktemp)
tmp_defaults=$(mktemp)
tmp_old=$(mktemp)
trap 'rm -f "$tmp_config" "$tmp_defaults" "$tmp_old"' EXIT HUP INT TERM

cat >"$tmp_old" <<'EOF'
# Keep WSL on the same clock as its Windows host through Hyper-V PTP.
# Public NTP pools remain available only as comparison sources.
refclock PHC /dev/ptp_hyperv poll 2 dpoll -2 prefer trust
EOF
if [ -e "$old_managed" ]; then
    if [ -L "$old_managed" ] || ! cmp -s "$old_managed" "$tmp_old"; then
        echo "Refusing to remove modified or symbolic old project file: $old_managed" >&2
        exit 2
    fi
    rm -f -- "$old_managed"
fi

{
    echo "# Managed by ydtrader setup_linux_time_sync.sh"
    echo "# Authority: $authority_name ($authority_url)"
    for server in $ntp_servers; do
        echo "server $server iburst"
    done
    echo "driftfile /var/lib/chrony/chrony.drift"
    echo "makestep 0.1 3"
    echo "rtcsync"
    echo "keyfile /etc/chrony/chrony.keys"
    echo "leapsectz right/UTC"
    echo "logdir /var/log/chrony"
} >"$tmp_config"
install -d -o root -g root -m 0755 /etc/chrony
install -o root -g root -m 0644 "$tmp_config" "$managed"

is_wsl=no
if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null || grep -qi microsoft /proc/version 2>/dev/null; then
    is_wsl=yes
fi
if [ -f "$defaults" ]; then
    awk -v is_wsl="$is_wsl" '
        /^[[:space:]]*DAEMON_OPTS=/ { print "DAEMON_OPTS=\"-F 1 -f /etc/chrony/ydtrader.conf\""; daemon = 1; next }
        /^[[:space:]]*SYNC_IN_CONTAINER=/ {
            if (is_wsl == "yes") print "SYNC_IN_CONTAINER=\"yes\""
            sync = 1
            next
        }
        { print }
        END {
            if (!daemon) print "DAEMON_OPTS=\"-F 1 -f /etc/chrony/ydtrader.conf\""
            if (is_wsl == "yes" && !sync) print "SYNC_IN_CONTAINER=\"yes\""
        }
    ' "$defaults" >"$tmp_defaults"
else
    echo 'DAEMON_OPTS="-F 1 -f /etc/chrony/ydtrader.conf"' >"$tmp_defaults"
    [ "$is_wsl" = yes ] && echo 'SYNC_IN_CONTAINER="yes"' >>"$tmp_defaults"
fi
install -o root -g root -m 0644 "$tmp_defaults" "$defaults"

systemctl disable --now systemd-timesyncd.service >/dev/null 2>&1 || true
systemctl enable chrony.service >/dev/null
systemctl restart chrony.service
sleep 2
if ps -C chronyd -o args= | grep -Eq '(^|[[:space:]])-[[:alnum:]]*x([[:space:]]|$)'; then
    echo "chronyd is running with -x and cannot control the clock." >&2
    exit 2
fi
chronyc burst 4/4 >/dev/null 2>&1 || true
chronyc makestep
chronyc waitsync 30 "$(awk "BEGIN { print $max_offset_ms / 1000 }")" 0.0 1

echo "Configured independent Linux synchronization."
echo "Authority: $authority_name"
echo "Website: $authority_url"
echo "NTP servers: $ntp_servers"
echo "WSL compatibility: $is_wsl"
chronyc sources -v
chronyc tracking

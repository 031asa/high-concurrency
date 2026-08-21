#!/bin/sh
set -eu

usage() {
    echo "Usage: $0 --config <time-authority.conf> --output <report.json>" >&2
    exit 1
}

[ "$#" -eq 4 ] || usage
config=
output=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --config) config=$2; shift 2 ;;
        --output) output=$2; shift 2 ;;
        *) usage ;;
    esac
done
[ -f "$config" ] || { echo "Config not found: $config" >&2; exit 1; }
[ -n "$output" ] || usage
command -v chronyd >/dev/null 2>&1 && command -v chronyc >/dev/null 2>&1 || {
    echo "chrony is not installed." >&2
    exit 1
}

config_value() {
    awk -F= -v wanted="$1" '$1 == wanted { sub(/^[^=]*=/, ""); gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; found=1 } END { if (!found) exit 1 }' "$config"
}
json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
json_words() {
    first=yes
    for word in $1; do
        [ "$first" = yes ] || printf ','
        printf '"%s"' "$(json_escape "$word")"
        first=no
    done
}

authority_name=$(config_value authority_name) || exit 1
authority_url=$(config_value authority_url) || exit 1
ntp_servers=$(config_value ntp_servers) || exit 1
environment=$(config_value environment) || exit 1
max_offset_ms=$(config_value max_offset_ms) || exit 1
max_cross_ms=$(config_value max_cross_difference_ms) || exit 1
case "$authority_url" in http://*|https://*) ;; *) echo "Invalid authority_url." >&2; exit 1;; esac
case "$environment" in test|production) ;; *) echo "Invalid environment." >&2; exit 1;; esac
case "$ntp_servers" in *REPLACE_*|*PLACEHOLDER*) echo "Refusing placeholder NTP configuration." >&2; exit 1;; esac
case "$max_offset_ms:$max_cross_ms" in *[!0-9:]*) echo "Invalid thresholds." >&2; exit 1;; esac
for server in $ntp_servers; do
    case "$server" in *[!A-Za-z0-9._:-]*|'') echo "Invalid NTP server: $server" >&2; exit 1;; esac
done

selected=NONE
selected_mode=NONE
sources=$(chronyc -c sources -n 2>/dev/null || true)
selected_line=$(printf '%s\n' "$sources" | awk -F, '$2 == "*" { print; exit }')
if [ -n "$selected_line" ]; then
    selected_mode=$(printf '%s' "$selected_line" | awk -F, '{print $1}')
    selected=$(printf '%s' "$selected_line" | awk -F, '{print $3}')
fi
tracking=$(chronyc -c tracking 2>/dev/null || true)
leap=$(printf '%s' "$tracking" | awk -F, 'NF >= 14 {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $14); print $14}')
dispersion_s=$(printf '%s' "$tracking" | awk -F, 'NF >= 12 {print $12}')
[ -n "$dispersion_s" ] || dispersion_s=0
dispersion_ms=$(awk -v value="$dispersion_s" 'BEGIN { printf "%.3f", value * 1000 }')

resolved=
probe_config=$(mktemp)
trap 'rm -f "$probe_config"' EXIT HUP INT TERM
for server in $ntp_servers; do
    echo "server $server iburst" >>"$probe_config"
    ips=$(getent ahosts "$server" 2>/dev/null | awk '{print $1}' | sort -u || true)
    resolved="$resolved $ips"
done
probe_output=$(LC_ALL=C chronyd -Q -t 20 -f "$probe_config" -U -u "$(id -un)" 2>&1 || true)
rm -f "$probe_config"
trap - EXIT HUP INT TERM
offset_s=$(printf '%s\n' "$probe_output" | sed -n 's/.*System clock wrong by \([-+0-9.]*\) seconds.*/\1/p' | tail -n 1)
failures=
if [ -z "$offset_s" ]; then
    offset_ms=0
    max_abs_ms=0
    failures="no valid NTP sample"
else
    offset_ms=$(awk -v value="$offset_s" 'BEGIN { printf "%.3f", value * 1000 }')
    max_abs_ms=$(awk -v value="$offset_ms" 'BEGIN { if (value < 0) value=-value; printf "%.3f", value }')
    if ! awk -v value="$max_abs_ms" -v limit="$max_offset_ms" 'BEGIN { exit !(value <= limit) }'; then
        failures="Linux/authority offset exceeds ${max_offset_ms} ms"
    fi
fi
if [ "$selected" = NONE ]; then failures="${failures}${failures:+; }chrony has no selected source"; fi
if [ "$selected_mode" = "#" ] || [ "$selected" = PHC0 ]; then failures="${failures}${failures:+; }chrony selected a local refclock instead of a network source"; fi
selected_allowed=no
for ip in $resolved; do [ "$selected" = "$ip" ] && selected_allowed=yes; done
if [ "$selected_allowed" != yes ]; then failures="${failures}${failures:+; }chrony selected source is not an address of the configured authority"; fi
if [ "$leap" != Normal ]; then failures="${failures}${failures:+; }chrony leap status is not Normal"; fi
pass=false
[ -z "$failures" ] && pass=true
generated=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
host=$(hostname)
resolved=$(printf '%s\n' $resolved | awk 'NF && !seen[$0]++ {printf "%s%s", sep, $0; sep=" "}')

out_dir=$(dirname "$output")
[ -d "$out_dir" ] || { echo "Output directory does not exist: $out_dir" >&2; exit 1; }
[ ! -L "$output" ] || { echo "Refusing symbolic-link output: $output" >&2; exit 1; }
tmp=$(mktemp "$out_dir/.ydtrader-time-report.XXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
{
    printf '{\n'
    printf '  "schema": 1,\n  "platform": "linux",\n'
    printf '  "hostname": "%s",\n  "generated_at_utc": "%s",\n' "$(json_escape "$host")" "$generated"
    printf '  "authority": {"name": "%s", "url": "%s", "ntp_servers": [' "$(json_escape "$authority_name")" "$(json_escape "$authority_url")"
    json_words "$ntp_servers"
    printf '], "environment": "%s"},\n' "$environment"
    printf '  "selected_source": "%s",\n  "selected_mode": "%s",\n' "$(json_escape "$selected")" "$(json_escape "$selected_mode")"
    printf '  "resolved_ips": ['; json_words "$resolved"; printf '],\n'
    printf '  "authority_minus_local_ms": %s,\n  "max_abs_sample_ms": %s,\n' "$offset_ms" "$max_abs_ms"
    printf '  "uncertainty_ms": %s,\n  "max_offset_ms": %s,\n  "max_cross_difference_ms": %s,\n' "$dispersion_ms" "$max_offset_ms" "$max_cross_ms"
    printf '  "pass": %s,\n  "failure": "%s"\n}\n' "$pass" "$(json_escape "$failures")"
} >"$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$output"
trap - EXIT HUP INT TERM

echo "=== Linux time authority report ==="
echo "Authority : $authority_name"
echo "Website   : $authority_url"
echo "NTP       : $ntp_servers"
echo "Selected  : $selected ($selected_mode)"
echo "Offset    : $offset_ms ms (authority minus Linux)"
echo "Report    : $output"
if [ "$pass" = true ]; then echo "RESULT: PASS"; exit 0; fi
echo "RESULT: FAIL"
echo "- $failures"
exit 2

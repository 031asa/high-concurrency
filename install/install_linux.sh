#!/bin/sh
set -eu
umask 027

[ "$#" -eq 0 ] || { echo "installer takes no arguments" >&2; exit 64; }
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 77; }

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
release_root=$(realpath -e -- "$script_dir/..")
source_app="$release_root/app"
manifest="$release_root/install_manifest.sha256"
install_root=/opt/ydtrader

[ -d "$source_app" ] && [ ! -L "$source_app" ] || exit 1
[ -f "$manifest" ] && [ ! -L "$manifest" ] || exit 1
[ ! -e "$install_root" ] || { echo "$install_root already exists" >&2; exit 1; }

(cd "$source_app" && sha256sum --strict -c "$manifest" >/dev/null)
cp -a -- "$source_app" "$install_root"
cp -- "$manifest" "$install_root/install_manifest.sha256"
chown -R root:root "$install_root"
find "$install_root" -type d -exec chmod 0755 {} +
find "$install_root" -type f -exec chmod 0644 {} +
chmod 0755 "$install_root/ydtrader"

mkdir -p "$install_root/logs" "$install_root/config"
chmod 0770 "$install_root/logs"
chmod 0750 "$install_root/config"
if [ -n "${SUDO_GID:-}" ]; then
    chgrp "$SUDO_GID" "$install_root/logs" "$install_root/config"
fi

echo "installed: $install_root/ydtrader"
echo "next: copy account.json and ydClient.ini into $install_root/config, then run ydtrader"

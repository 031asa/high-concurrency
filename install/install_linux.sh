#!/bin/sh
set -eu
umask 027

[ "$#" -eq 0 ] || { echo "installer takes no arguments" >&2; exit 64; }
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 77; }

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
release_root=$(realpath -e -- "$script_dir/..")
source_app="$release_root/app"
manifest="$release_root/install_manifest.sha256"
signature="$release_root/install_manifest.sig"
public_key="$script_dir/ydtrader-public.pem"
destroy_source="$script_dir/ydtrader-destroy"
install_root=/opt/ydtrader

[ -d "$source_app" ] && [ ! -L "$source_app" ] || exit 1
[ -f "$manifest" ] && [ -f "$signature" ] && [ -f "$public_key" ] || exit 1
[ -f "$destroy_source" ] && [ ! -L "$destroy_source" ] || exit 1
[ ! -e "$install_root" ] || { echo "$install_root already exists" >&2; exit 1; }

openssl pkeyutl -verify -pubin -inkey "$public_key" -rawin \
    -in "$manifest" -sigfile "$signature" >/dev/null
(cd "$source_app" && sha256sum --strict -c "$manifest" >/dev/null)

mkdir -p /usr/local/libexec
install -o root -g root -m 0755 "$destroy_source" /usr/local/libexec/ydtrader-destroy
install -o root -g root -m 0644 "$public_key" /usr/local/libexec/ydtrader-public.pem

cp -a -- "$source_app" "$install_root"
cp -- "$manifest" "$install_root/install_manifest.sha256"
cp -- "$signature" "$install_root/install_manifest.sig"
printf '%s\n' ydtrader-install-v1 >"$install_root/.ydtrader-install-marker"
chown -R root:root "$install_root"
find "$install_root" -type d -exec chmod 0755 {} +
find "$install_root" -type f -exec chmod 0644 {} +
chmod 0755 "$install_root/ydtrader"
chmod 0600 "$install_root/.ydtrader-install-marker" "$install_root/install_manifest.sig"

mkdir -p "$install_root/logs" "$install_root/config"
chmod 0770 "$install_root/logs"
chmod 0750 "$install_root/config"
if [ -n "${SUDO_GID:-}" ]; then
    chgrp "$SUDO_GID" "$install_root/logs" "$install_root/config"
fi

echo "installed: $install_root/ydtrader"
echo "next: copy account.json and ydClient.ini into $install_root/config, then activate"

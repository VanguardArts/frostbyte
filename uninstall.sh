#!/usr/bin/env bash
#
# FrostByte uninstaller. Run with:  sudo ./uninstall.sh
# Env:  KEEP_DRIVER=1  keep the acer-wmi-linuwu DKMS driver
#       PURGE=1        also delete /etc/frostbyte config
#
set -euo pipefail

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

say "Stopping daemon"
systemctl disable --now frostbyte-daemon.service 2>/dev/null || true
rm -f /etc/systemd/system/frostbyte-daemon.service
systemctl daemon-reload

say "Removing app files"
rm -rf /opt/frostbyte
rm -f /usr/local/bin/frostctl /usr/local/bin/frostbyte
rm -f /usr/share/applications/frostbyte.desktop
rm -f /usr/share/pixmaps/frostbyte.svg
rm -f /run/frostbyte.sock

if [[ "${KEEP_DRIVER:-0}" != "1" ]]; then
    say "Removing kernel driver"
    modprobe -r acer_wmi 2>/dev/null || true
    ver=$(dkms status acer-wmi-linuwu 2>/dev/null | head -1 | cut -d, -f1 | cut -d/ -f2 || true)
    [[ -n "${ver:-}" ]] && dkms remove "acer-wmi-linuwu/$ver" --all || true
    depmod -a || true
    echo "  (the in-tree acer_wmi driver will be used after reboot)"
fi

if [[ "${PURGE:-0}" == "1" ]]; then
    rm -rf /etc/frostbyte
    say "Config purged"
fi

say "FrostByte removed."

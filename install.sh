#!/usr/bin/env bash
#
# FrostByte - Open-source fan & thermal control for Acer Nitro/Predator laptops
# Copyright (C) 2026 FrostByte contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# FrostByte installer. Run with:  sudo ./install.sh
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER_DIR="$here/driver/acer-wmi-linuwu"
PREFIX="/opt/frostbyte"
BIN="/usr/local/bin"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "please run as root: sudo ./install.sh"

# ---------------------------------------------------------------- dependencies
# Detects the distro's package manager so the installer works on Debian/Ubuntu,
# Fedora/RHEL, Arch, and openSUSE (all systemd-based). Package names for the
# same dependency differ (python3-pyqt6 vs python-pyqt6, etc.).
say "Checking dependencies"

PM=""
if   command -v apt-get >/dev/null 2>&1; then PM="apt"
elif command -v dnf     >/dev/null 2>&1; then PM="dnf"
elif command -v pacman  >/dev/null 2>&1; then PM="pacman"
elif command -v zypper  >/dev/null 2>&1; then PM="zypper"
fi

need_install=()
command -v dkms    >/dev/null 2>&1 || need_install+=("dkms")
command -v gcc     >/dev/null 2>&1 || need_install+=("gcc")
command -v make    >/dev/null 2>&1 || need_install+=("make")
command -v python3 >/dev/null 2>&1 || need_install+=("python3")
if ! python3 -c 'import PyQt6' 2>/dev/null; then
    if [[ "$PM" == "pacman" ]]; then need_install+=("python-pyqt6")
    else need_install+=("python3-pyqt6"); fi
fi

# Kernel headers are needed for DKMS and are named differently per distro;
# install them separately so a header-package hiccup can't roll back the rest.
headers_missing=0
[[ -e "/lib/modules/$(uname -r)/build/Makefile" ]] || headers_missing=1

if ((${#need_install[@]})) || [[ $headers_missing -eq 1 ]]; then
    case "$PM" in
        apt)
            apt-get update -qq
            ((${#need_install[@]})) && apt-get install -y "${need_install[@]}"
            [[ $headers_missing -eq 1 ]] && apt-get install -y "linux-headers-$(uname -r)"
            ;;
        dnf)
            ((${#need_install[@]})) && dnf install -y --refresh "${need_install[@]}"
            if [[ $headers_missing -eq 1 ]]; then
                dnf install -y "kernel-devel-$(uname -r)" 2>/dev/null || dnf install -y kernel-devel
            fi
            ;;
        pacman)
            pacman -Sy --noconfirm
            ((${#need_install[@]})) && pacman -S --needed --noconfirm "${need_install[@]}"
            [[ $headers_missing -eq 1 ]] && pacman -S --needed --noconfirm linux-headers
            ;;
        zypper)
            ((${#need_install[@]})) && zypper --non-interactive install "${need_install[@]}"
            [[ $headers_missing -eq 1 ]] && zypper --non-interactive install kernel-devel
            ;;
        *)
            die "no supported package manager found (need apt/dnf/pacman/zypper)"
            ;;
    esac
fi
ok "dependencies satisfied"

# ------------------------------------------------- clean up DAMX/Linuwu debris
say "Removing leftover Linuwu-Sense/DAMX driver config"
rm -f /etc/modprobe.d/blacklist-acer_wmi.conf \
      /etc/modprobe.d/linuwu-sense.conf \
      /etc/modules-load.d/linuwu_sense.conf
rm -f /usr/lib/modules/*/kernel/drivers/platform/x86/linuwu_sense.ko* 2>/dev/null || true
if systemctl list-unit-files | grep -q '^damx-daemon.service'; then
    systemctl disable --now damx-daemon.service >/dev/null 2>&1 || true
    ok "DAMX daemon stopped & disabled (app files left untouched)"
fi
if systemctl list-unit-files | grep -q '^nitro-key-detection.service'; then
    systemctl disable --now nitro-key-detection.service >/dev/null 2>&1 || true
fi
modprobe -r linuwu_sense 2>/dev/null || true
ok "old driver config cleaned"

# ---------------------------------------------------------------- kernel driver
say "Installing acer-wmi-linuwu kernel driver via DKMS"
[[ -f "$DRIVER_DIR/dkms.conf" ]] || die "driver source missing: $DRIVER_DIR"
DRV_NAME="$(grep -Po 'PACKAGE_NAME="\K[^"]+' "$DRIVER_DIR/dkms.conf")"
DRV_VER="$(grep -Po 'PACKAGE_VERSION="\K[^"]+' "$DRIVER_DIR/dkms.conf")"
if dkms status "$DRV_NAME/$DRV_VER" | grep -q .; then
    dkms remove "$DRV_NAME/$DRV_VER" --all >/dev/null 2>&1 || true
fi
dkms add "$DRIVER_DIR"
dkms build "$DRV_NAME/$DRV_VER"
dkms install "$DRV_NAME/$DRV_VER"
ok "DKMS installed $DRV_NAME $DRV_VER (auto-rebuilds on kernel updates)"

modprobe -r acer_wmi 2>/dev/null || true
modprobe acer_wmi
sleep 1
if [[ -e /sys/devices/platform/acer-wmi/nitro_sense/fan_speed \
   || -e /sys/devices/platform/acer-wmi/predator_sense/fan_speed ]]; then
    ok "fan control interface is live"
else
    die "driver loaded but no nitro_sense interface appeared - check dmesg"
fi

# ---------------------------------------------------------------- app files
say "Installing FrostByte to $PREFIX"
mkdir -p "$PREFIX" /etc/frostbyte
cp "$here/common/frostbyte_common.py" "$PREFIX/"
cp "$here/common/frostbyte_gpu.py"     "$PREFIX/"
cp "$here/daemon/frostbyte-daemon.py" "$PREFIX/"
cp "$here/cli/frostctl.py"            "$PREFIX/"
cp "$here/cli/frostbyte-gpu-run"      "$PREFIX/"
cp "$here/gui/frostbyte_gui.py"       "$PREFIX/"
cp "$here/gui/frostbyte.svg"          "$PREFIX/"
cp "$here/keyd/frostbyte-keyd.py"     "$PREFIX/"
chmod 755 "$PREFIX/frostbyte-daemon.py" "$PREFIX/frostctl.py" \
          "$PREFIX/frostbyte_gui.py" "$PREFIX/frostbyte-keyd.py" \
          "$PREFIX/frostbyte-gpu-run"

# Default hotkey config (kernel default 425 + hwdb prog1 remap 148)
if [[ ! -e /etc/frostbyte/frostbyte.conf ]]; then
    install -Dm644 "$here/etc/frostbyte.conf" /etc/frostbyte/frostbyte.conf
fi

ln -sf "$PREFIX/frostctl.py" "$BIN/frostctl"
ln -sf "$PREFIX/frostbyte-gpu-run" "$BIN/frostbyte-gpu-run"
ln -sf "$PREFIX/frostbyte-keyd.py" "$BIN/frostbyte-keyd"
cat > "$BIN/frostbyte" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /opt/frostbyte/frostbyte_gui.py "$@"
EOF
chmod 755 "$BIN/frostbyte"
ok "frostctl + frostbyte installed"

cp "$here/systemd/frostbyte-daemon.service" /etc/systemd/system/
cp "$here/systemd/frostbyte-keyd.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now frostbyte-daemon.service
systemctl enable --now frostbyte-keyd.service
ok "daemon + NitroSense key handler enabled and started"

install -Dm644 "$here/gui/frostbyte.svg" /usr/share/pixmaps/frostbyte.svg
install -Dm644 "$here/desktop/frostbyte.desktop" \
    /usr/share/applications/frostbyte.desktop
ok "desktop entry installed"

# ---------------------------------------------------------------- smoke test
say "Smoke test"
sleep 2
if "$BIN/frostctl" status; then
    ok "FrostByte is operational"
else
    warn "daemon not answering yet - check: journalctl -u frostbyte-daemon -e"
fi

cat <<'BANNER'

  ❄ FrostByte installed.
    GUI:     run "frostbyte" or find FrostByte in the app menu
    CLI:     frostctl status | frostctl max | frostctl auto | frostctl fans 60 60
    Hotkey:  the NitroSense 'N' key toggles the GUI (frostbyte-keyd.service)
             if it seems dead, run: sudo frostbyte-keyd --learn
    Logs:    journalctl -u frostbyte-daemon -f
    Config:  /etc/frostbyte/frostbyte.conf

BANNER

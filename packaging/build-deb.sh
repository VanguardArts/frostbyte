#!/usr/bin/env bash
#
# Build the FrostByte .deb package into dist/.
# Usage: ./packaging/build-deb.sh   (run from the repo root; no root needed)
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="frostbyte"
VER="$(grep -Po 'FROSTBYTE_VERSION = "\K[^"]+' "$here/common/frostbyte_common.py")"
REL="1"
DRV_VER="$(grep -Po 'PACKAGE_VERSION="\K[^"]+' "$here/driver/acer-wmi-linuwu/dkms.conf")"
STAGE="$here/dist/deb-stage"
OUT="$here/dist/${PKG}_${VER}-${REL}_all.deb"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/lib/frostbyte" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/lib/systemd/system" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/pixmaps" \
         "$STAGE/usr/share/frostbyte" \
         "$STAGE/usr/share/doc/frostbyte" \
         "$STAGE/usr/src/acer-wmi-linuwu-${DRV_VER}"

# --- maintainer scripts -----------------------------------------------------
for f in control postinst prerm postrm; do
    install -Dm644 "$here/packaging/debian/$f" "$STAGE/DEBIAN/$f"
done
sed -i "s/^Version: .*/Version: ${VER}-${REL}/" "$STAGE/DEBIAN/control"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"

# --- application ------------------------------------------------------------
install -m644 "$here/common/frostbyte_common.py" "$STAGE/usr/lib/frostbyte/"
install -m755 "$here/daemon/frostbyte-daemon.py" "$STAGE/usr/lib/frostbyte/"
install -m755 "$here/cli/frostctl.py"            "$STAGE/usr/lib/frostbyte/"
install -m755 "$here/gui/frostbyte_gui.py"       "$STAGE/usr/lib/frostbyte/"
install -m755 "$here/keyd/frostbyte-keyd.py"     "$STAGE/usr/lib/frostbyte/"
install -m644 "$here/gui/frostbyte.svg"          "$STAGE/usr/lib/frostbyte/"

cat > "$STAGE/usr/bin/frostbyte" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/frostbyte/frostbyte_gui.py "$@"
EOF
chmod 755 "$STAGE/usr/bin/frostbyte"
ln -s ../lib/frostbyte/frostctl.py        "$STAGE/usr/bin/frostctl"
ln -s ../lib/frostbyte/frostbyte-keyd.py  "$STAGE/usr/bin/frostbyte-keyd"

# --- systemd, desktop, icon, config template --------------------------------
install -m644 "$here/systemd/frostbyte-daemon.service" "$STAGE/usr/lib/systemd/system/"
install -m644 "$here/systemd/frostbyte-keyd.service"   "$STAGE/usr/lib/systemd/system/"
# units reference /opt paths in-tree; point them at /usr/lib/frostbyte
sed -i 's|/opt/frostbyte|/usr/lib/frostbyte|g' \
    "$STAGE/usr/lib/systemd/system/"frostbyte-*.service
install -m644 "$here/desktop/frostbyte.desktop" "$STAGE/usr/share/applications/"
install -m644 "$here/gui/frostbyte.svg"         "$STAGE/usr/share/pixmaps/"
install -m644 "$here/etc/frostbyte.conf"        "$STAGE/usr/share/frostbyte/"

# --- docs ---------------------------------------------------------------------
install -m644 "$here/README.md" "$STAGE/usr/share/doc/frostbyte/"
install -m644 "$here/packaging/debian/copyright" "$STAGE/usr/share/doc/frostbyte/"

# --- vendored kernel driver source for DKMS -----------------------------------
cp -a "$here/driver/acer-wmi-linuwu/." \
      "$STAGE/usr/src/acer-wmi-linuwu-${DRV_VER}/"

# --- build ---------------------------------------------------------------------
say "Building $OUT"
mkdir -p "$here/dist"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
rm -rf "$STAGE"
say "Done: $OUT"
dpkg-deb --info "$OUT" | grep -E 'Package|Version|Architecture|Depends|Installed-Size' || true

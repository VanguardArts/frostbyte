#!/usr/bin/env bash
#
# Build the FrostByte .rpm inside a Fedora container (or with local rpmbuild).
# Producing it in Fedora yields a correctly distro-tagged .fcXX.noarch.rpm.
# Run from the repo root; no root needed (uses podman/docker).
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(grep -Po 'FROSTBYTE_VERSION = "\K[^"]+' "$here/common/frostbyte_common.py")"
SPEC="$here/packaging/fedora/frostbyte.spec"
SRC_TARBALL="$here/dist/frostbyte-$VER.tar.gz"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

mkdir -p "$here/dist"

# Source tarball (HEAD must be committed for the packaging files to appear).
say "Bundling source tarball for $VER"
git -C "$here" archive --format=tar.gz --prefix=frostbyte-$VER/ \
    -o "$SRC_TARBALL" HEAD

if command -v podman >/dev/null 2>&1; then RT=podman
elif command -v docker >/dev/null 2>&1; then RT=docker
else RT=""; fi

build_inside() {
    # $1 = container runtime binary (podman|docker)
    "$1" run --rm \
        -v "$here":/src:z -w /src \
        fedora:latest bash -c '
            set -euo pipefail
            dnf install -y --refresh rpm-build dkms kmod python3 python3-pyqt6 >/dev/null
            mkdir -p /root/rpmbuild/SOURCES
            cp '"dist/frostbyte-$VER.tar.gz"' /root/rpmbuild/SOURCES/
            # pin the spec Version to the project version
            sed -i "s/^Version:.*/Version: '"$VER"'/" packaging/fedora/frostbyte.spec
            rpmbuild -ba --define "_topdir /root/rpmbuild" packaging/fedora/frostbyte.spec
            mkdir -p dist
            cp /root/rpmbuild/RPMS/noarch/*.rpm dist/
        '
}

if [[ -n "$RT" ]]; then
    say "Building in a Fedora container via $RT"
    if $RT info >/dev/null 2>&1; then
        build_inside "$RT"
        ls -la "$here"/dist/*.rpm
        exit 0
    fi
    say "$RT not usable, falling back to local rpmbuild"
fi

if command -v rpmbuild >/dev/null 2>&1; then
    say "Building with local rpmbuild (untagged .rpm)"
    _topdir="$(mktemp -d)"
    mkdir -p "$_topdir"/{SOURCES,BUILD,RPMS,SRPMS,SPECS,BUILDROOT}
    cp "$SRC_TARBALL" "$_topdir/SOURCES/"
    sed "s/^Version:.*/Version: $VER/" "$SPEC" > "$_topdir/SPECS/frostbyte.spec"
    rpmbuild -ba \
        --define "_topdir $_topdir" \
        --define "%_unitdir /usr/lib/systemd/system" \
        "$_topdir/SPECS/frostbyte.spec" || {
            echo "local rpmbuild failed (see above)"; rm -rf "$_topdir"; exit 1; }
    mkdir -p "$here/dist"
    cp "$_topdir"/RPMS/noarch/*.rpm "$here/dist/"
    rm -rf "$_topdir"
    ls -la "$here"/dist/*.rpm
    exit 0
fi

echo "ERROR: need podman, docker, or rpmbuild to build the RPM" >&2
exit 1

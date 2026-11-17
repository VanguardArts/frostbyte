#!/usr/bin/env bash
#
# Build the Arch Linux package (.pkg.tar.zst) inside an Arch container via
# makepkg, using the same toolchain Arch users rely on.
# Run from the repo root; no root needed (uses podman/docker).
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(grep -Po 'FROSTBYTE_VERSION = "\K[^"]+' "$here/common/frostbyte_common.py")"

if command -v podman >/dev/null 2>&1; then RT=podman
elif command -v docker >/dev/null 2>&1; then RT=docker
else echo "ERROR: need podman or docker to build the Arch package" >&2; exit 1; fi

mkdir -p "$here/dist"

# Stage a local source tarball so the build works before the tag is published
# (makepkg finds this and skips downloading the GitHub archive). Cleaned up
# afterwards.
git -C "$here" archive --format=tar.gz --prefix=frostbyte-$VER/ \
    -o "$here/packaging/arch/frostbyte-$VER.tar.gz" HEAD

"$RT" run --rm \
    -v "$here/packaging/arch":/src:ro \
    -v "$here/dist":/out \
    archlinux:latest bash -c '
        set -euo pipefail
        pacman -Sy --noconfirm --needed base-devel git >/dev/null
        useradd -m builder
        cp -a /src /build
        chown -R builder:builder /build
        su builder -c "
            set -euo pipefail
            cd /build
            sed -i \"s/^pkgver=.*/pkgver='"$VER"'/\" PKGBUILD
            makepkg -f --nodeps --noconfirm
        "
        cp /build/*.pkg.tar.* /out/ 2>/dev/null || true
    '

rm -f "$here/packaging/arch/frostbyte-$VER.tar.gz"
ls -la "$here"/dist/*.pkg.tar.* || { echo "no package produced"; exit 1; }

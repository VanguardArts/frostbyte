#!/usr/bin/env bash
#
# Build the Arch Linux package (.pkg.tar.zst) inside an Arch container via
# makepkg, so it is produced by the same toolchain Arch users use.
# Run from the repo root; no root needed (uses podman/docker).
#
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(grep -Po 'FROSTBYTE_VERSION = "\K[^"]+' "$here/common/frostbyte_common.py")"

if command -v podman >/dev/null 2>&1; then RT=podman
elif command -v docker >/dev/null 2>&1; then RT=docker
else echo "ERROR: need podman or docker to build the Arch package" >&2; exit 1; fi

# makepkg refuses to run as root; build as the container's non-root user.
"$RT" run --rm \
    -v "$here":/src:z -w /src \
    archlinux:latest bash -c '
        set -euo pipefail
        pacman -Sy --noconfirm --needed base-devel git >/dev/null
        useradd -m builder
        chown -R builder:builder /src
        su builder -c "
            set -euo pipefail
            cd /src/packaging/arch
            sed -i \"s/^pkgver=.*/pkgver='"$VER"'/\" PKGBUILD
            makepkg -sf --noconfirm
        "
        mkdir -p /src/dist
        cp /src/packaging/arch/*.pkg.tar.* /src/dist/
    '
ls -la "$here"/dist/*.pkg.tar.* 2>/dev/null || { echo "no package produced"; exit 1; }

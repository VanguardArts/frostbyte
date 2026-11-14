#!/usr/bin/env python3
#
# FrostByte - Open-source fan & thermal control for Acer Nitro/Predator laptops
# Copyright (C) 2026 FrostByte contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
"""Shared constants and the Unix-socket client used by frostctl and the GUI."""

import json
import os
import socket
import sys

FROSTBYTE_VERSION = "1.2.0"

SOCKET_PATH = "/run/frostbyte.sock"
CONFIG_PATH = "/etc/frostbyte/frostbyte.conf"
LOG_PATH = "/var/log/frostbyte.log"

# Fan control modes
MODE_AUTO = "auto"        # firmware (EC) controls the fans
MODE_MANUAL = "manual"    # fixed percentages chosen by the user
MODE_MAX = "max"          # firmware turbo / maximum speed
MODE_CURVE = "curve"      # daemon applies the user-defined curves

ALL_MODES = (MODE_AUTO, MODE_MANUAL, MODE_MAX, MODE_CURVE)


class DaemonError(Exception):
    """Raised when the daemon is unreachable or returns an error."""


def request(payload: dict, timeout: float = 5.0) -> dict:
    """Send one JSON request to the daemon and return its reply dict.

    Raises DaemonError on any failure.
    """
    if not os.path.exists(SOCKET_PATH):
        raise DaemonError(
            f"daemon socket {SOCKET_PATH} not found - is frostbyte-daemon running?"
        )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(SOCKET_PATH)
            sock.sendall((json.dumps(payload) + "\n").encode())
            chunks = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
                if b"\n" in data:
                    break
    except (OSError, socket.timeout) as exc:
        raise DaemonError(f"cannot reach daemon: {exc}") from exc

    raw = b"".join(chunks).strip()
    if not raw:
        raise DaemonError("empty reply from daemon")
    try:
        reply = json.loads(raw.decode())
    except json.JSONDecodeError as exc:
        raise DaemonError(f"garbled reply from daemon: {raw[:120]!r}") from exc

    if not reply.get("ok", False):
        raise DaemonError(reply.get("error", "unknown daemon error"))
    return reply


def get_status(timeout: float = 5.0) -> dict:
    return request({"cmd": "get_status"}, timeout)["status"]


def daemon_available() -> bool:
    try:
        request({"cmd": "ping"}, timeout=1.5)
        return True
    except DaemonError:
        return False


def install_dir() -> str:
    """Directory holding the FrostByte code (works both installed and in-tree)."""
    return os.path.dirname(os.path.abspath(sys.argv[0]))

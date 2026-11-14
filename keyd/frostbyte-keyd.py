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
"""
frostbyte-keyd - watches for the dedicated NitroSense/PredatorSense key
and toggles the FrostByte GUI in the user's graphical session.

The key is an ordinary PS/2 keyboard scancode (0xf5 on Nitro models),
mapped by the kernel to keycode 425 (KEY_EMOJI_PICKER) and remapped by
some systemd hwdb versions to 148 (KEY_PROG1). We accept both by default;
override in /etc/frostbyte/frostbyte.conf:

    [hotkey]
    keycodes = 425,148
    enabled = true

Runs as root (evdev devices are root:input). Uses only the Python stdlib.
"""

import configparser
import glob
import logging
import logging.handlers
import os
import pwd
import select
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frostbyte_common import CONFIG_PATH  # noqa: E402

EVENT = struct.Struct("llHHi")          # struct input_event (64-bit)
EV_KEY = 0x01
EVIOCGRAB = 0x40044590                  # exclusive-grab ioctl
DEFAULT_KEYCODES = {425, 148}           # kernel default + hwdb prog1 remap
DEBOUNCE_SECONDS = 1.5
RESCAN_SECONDS = 10.0
LOG_PATH = "/var/log/frostbyte-keyd.log"

log = logging.getLogger("frostbyte-keyd")


def load_settings():
    keycodes = set(DEFAULT_KEYCODES)
    enabled = True
    parser = configparser.ConfigParser()
    try:
        parser.read(CONFIG_PATH)
        if parser.has_option("hotkey", "keycodes"):
            vals = parser.get("hotkey", "keycodes")
            parsed = {int(v.strip()) for v in vals.split(",") if v.strip()}
            if parsed:
                keycodes = parsed
        if parser.has_option("hotkey", "enabled"):
            enabled = parser.getboolean("hotkey", "enabled", fallback=True)
    except (configparser.Error, ValueError) as exc:
        log.error("config error, using defaults: %s", exc)
    return enabled, keycodes


def open_keyboards():
    """Open every keyboard-ish evdev device. Returns {fd: (path, name)}."""
    fds = {}
    for path in sorted(glob.glob("/dev/input/event*")):
        base = os.path.basename(path)
        try:
            with open(f"/sys/class/input/{base}/device/name",
                      encoding="utf-8") as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if not any(hint in name.lower()
                   for hint in ("keyboard", "wmi hotkeys", "at translated")):
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        fds[fd] = (path, name)
    return fds


def find_session_user():
    """Best-effort: the locally logged-in graphical user (name, uid)."""
    try:
        out = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            cols = line.split()
            if len(cols) >= 3 and cols[2] not in ("root", "gdm", "sddm"):
                try:
                    pw = pwd.getpwnam(cols[2])
                except KeyError:
                    continue
                if os.path.isdir(f"/run/user/{pw.pw_uid}"):
                    return pw.pw_name, pw.pw_uid
    except (OSError, subprocess.SubprocessError):
        pass
    for runtime in sorted(glob.glob("/run/user/[0-9]*")):
        uid = int(os.path.basename(runtime))
        if uid >= 1000:
            try:
                return pwd.getpwuid(uid).pw_name, uid
            except KeyError:
                continue
    return None, None


def launch_gui():
    """Start (or toggle) the FrostByte GUI inside the user's session."""
    user, uid = find_session_user()
    if not user:
        log.error("no graphical session user found; cannot launch GUI")
        return
    runtime = f"/run/user/{uid}"
    waylands = sorted(glob.glob(os.path.join(runtime, "wayland-*")))
    env = {
        "HOME": pwd.getpwnam(user).pw_dir,
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    if waylands:
        env["WAYLAND_DISPLAY"] = os.path.basename(waylands[0])
    env["DISPLAY"] = ":0"  # XWayland fallback
    xauth = glob.glob(os.path.join(runtime, "xauth_*")) + \
        [os.path.join(env["HOME"], ".Xauthority")]
    for cand in xauth:
        if os.path.isfile(cand):
            env["XAUTHORITY"] = cand
            break
    log.info("NitroSense key pressed - toggling FrostByte for user %s", user)
    subprocess.Popen(
        ["runuser", "-u", user, "--", "env", *(
            f"{k}={v}" for k, v in env.items()), "frostbyte"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)


def learn_mode(seconds=20):
    """Interactively discover the NitroSense key code.

    Grabs all keyboard-ish devices EXCLUSIVELY (keystrokes will not reach
    applications while learning) and prints every key event with its code.
    """
    import fcntl
    if os.geteuid() != 0:
        print("learn mode needs root (evdev is root-only) - run with sudo")
        sys.exit(1)
    fds = open_keyboards()
    if not fds:
        print("no keyboard devices found")
        return
    print("FrostByte hotkey learn mode")
    print("---------------------------")
    for path, name in fds.values():
        print(f"  grabbed {path}: {name}")
    print(f"\nThe keyboard is exclusively grabbed for {seconds}s - keystrokes")
    print("will NOT reach other apps. Press the NitroSense 'N' key now.\n")
    for fd in fds:
        try:
            fcntl.ioctl(fd, EVIOCGRAB, 1)
        except OSError as exc:
            print(f"  (could not grab {fds[fd][0]}: {exc})")
    end = time.time() + seconds
    seen = False
    try:
        while time.time() < end:
            r, _, _ = select.select(list(fds), [], [], 0.5)
            for fd in r:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    continue
                for off in range(0, len(data) - 23, 24):
                    _s, _u, etype, code, value = EVENT.unpack_from(data, off)
                    if etype == EV_KEY:
                        seen = True
                        kind = {0: "release", 1: "PRESS", 2: "repeat"}.get(
                            value, value)
                        print(f"  key event: code={code} {kind} "
                              f"[{fds[fd][1]}]", flush=True)
    finally:
        for fd in fds:
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
            except OSError:
                pass
            os.close(fd)
    if not seen:
        print("\nNo key events at all - the key is not reaching the OS.")
    print("\nSet the PRESS code in /etc/frostbyte/frostbyte.conf:")
    print("  [hotkey]\n  keycodes = <code>\n")


def main():
    if "--learn" in sys.argv:
        learn_mode()
        return
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=500_000, backupCount=1))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers)

    if os.geteuid() != 0:
        log.error("frostbyte-keyd must run as root")
        sys.exit(1)

    enabled, keycodes = load_settings()
    if not enabled:
        log.info("hotkey disabled in config; exiting")
        return
    log.info("watching for NitroSense key, keycodes=%s",
             ",".join(map(str, sorted(keycodes))))

    fds = {}
    last_rescan = 0.0
    last_fire = 0.0

    while True:
        now = time.monotonic()
        if now - last_rescan > RESCAN_SECONDS or not fds:
            for fd in list(fds):
                os.close(fd)
            fds = open_keyboards()
            last_rescan = now
            if not fds:
                log.warning("no keyboard devices found; retrying")
                time.sleep(RESCAN_SECONDS)
                continue

        try:
            r, _, _ = select.select(list(fds), [], [], RESCAN_SECONDS)
        except OSError:
            continue
        for fd in r:
            path, name = fds[fd]
            try:
                data = os.read(fd, 4096)
            except OSError:
                os.close(fd)
                del fds[fd]
                continue
            if not data:
                continue
            for off in range(0, len(data) - 23, 24):
                _s, _u, etype, code, value = EVENT.unpack_from(data, off)
                if etype != EV_KEY or value != 1:      # presses only
                    continue
                if code in keycodes:
                    if time.monotonic() - last_fire < DEBOUNCE_SECONDS:
                        continue
                    last_fire = time.monotonic()
                    try:
                        launch_gui()
                    except Exception:
                        log.exception("failed to launch GUI")


if __name__ == "__main__":
    main()
# (main() handles --learn before doing anything else)

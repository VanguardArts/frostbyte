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
"""frostctl - command line interface to the FrostByte daemon."""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frostbyte_common as fb  # noqa: E402


def fmt_temp(t):
    return "--.-" if t is None else f"{t:5.1f}"


def fmt_rpm(r):
    return "----" if r is None else f"{r:4d}"


def cmd_status(_args):
    s = fb.get_status()
    print(f"FrostByte {s['daemon_version']}  |  driver: "
          f"{s['driver_name'] or 'MISSING'} "
          f"({s['driver_version'] or 'not loaded'})")
    if not s["driver_present"]:
        print("!! acer-wmi sense interface not found - run install.sh "
              "or check `dkms status`")
    print(f"Mode: {s['mode']}   Profile: {s['profile']}   "
          f"Power: {'AC' if s['ac_online'] else 'Battery' if s['ac_online'] is False else '?'}")
    if s["safety_override"]:
        print("!! SAFETY OVERRIDE ACTIVE (temperature critical, fans forced 100%)")
    cpu, gpu = s["fan_setting"]
    print(f"CPU fan: {fmt_temp(s['cpu_temp'])} C  {fmt_rpm(s['cpu_rpm'])} RPM"
          f"  set: {'AUTO' if cpu == 0 else cpu}")
    print(f"GPU fan: {fmt_temp(s['gpu_temp'])} C  {fmt_rpm(s['gpu_rpm'])} RPM"
          f"  set: {'AUTO' if gpu == 0 else gpu}")
    if s.get("battery_limiter") is not None:
        print(f"Battery limiter (80%): {'ON' if s['battery_limiter'] else 'OFF'}")


def cmd_fans(args):
    fb.request({"cmd": "set_fans", "cpu": args.cpu, "gpu": args.gpu})
    print(f"Manual mode: CPU fan {args.cpu}%, GPU fan {args.gpu}%")


def cmd_mode(mode):
    fb.request({"cmd": "set_mode", "mode": mode})
    print(f"Mode set to {mode}")


def cmd_profile(args):
    fb.request({"cmd": "set_profile", "profile": args.profile})
    print(f"Platform profile set to {args.profile}")


def cmd_limiter(args):
    fb.request({"cmd": "set_limiter", "enabled": args.state == "on"})
    print(f"Battery limiter {'ON' if args.state == 'on' else 'OFF'}")


def cmd_curves(_args):
    s = fb.get_status()
    for which in ("cpu", "gpu"):
        pts = s["config"][f"{which}_curve"]
        print(f"{which.upper()}: " + ", ".join(f"{t}C->{p}%" for t, p in pts))


def cmd_set_curve(args):
    points = []
    for pair in args.points.split(","):
        t, p = pair.strip().split(":")
        points.append([int(t), int(p)])
    fb.request({"cmd": "set_curve", "which": args.which, "points": points})
    print(f"{args.which.upper()} curve updated: "
          + ", ".join(f"{t}C->{p}%" for t, p in points))


def cmd_watch(_args):
    try:
        while True:
            s = fb.get_status()
            cpu, gpu = s["fan_setting"]
            line = (f"\r{time.strftime('%H:%M:%S')}  "
                    f"CPU {fmt_temp(s['cpu_temp'])}C {fmt_rpm(s['cpu_rpm'])}RPM "
                    f"({('AUTO' if cpu == 0 else str(cpu) + '%'):>4})   "
                    f"GPU {fmt_temp(s['gpu_temp'])}C {fmt_rpm(s['gpu_rpm'])}RPM "
                    f"({('AUTO' if gpu == 0 else str(gpu) + '%'):>4})   "
                    f"[{s['mode']}]")
            print(line, end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()


def build_parser():
    p = argparse.ArgumentParser(
        prog="frostctl",
        description="FrostByte fan & thermal control CLI "
                    "(talks to frostbyte-daemon)")
    p.add_argument("--version", action="version",
                   version=f"FrostByte {fb.FROSTBYTE_VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("status", help="show full status")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("fans", help="manual mode: set CPU/GPU fan %% (0=auto)")
    sp.add_argument("cpu", type=int)
    sp.add_argument("gpu", type=int)
    sp.set_defaults(func=cmd_fans)

    for mode in ("auto", "max", "curve"):
        sp = sub.add_parser(mode, help=f"switch to {mode} mode")
        sp.set_defaults(func=lambda a, m=mode: cmd_mode(m))

    sp = sub.add_parser("profile", help="set platform thermal profile")
    sp.add_argument("profile", help="e.g. quiet, balanced, performance")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("limiter", help="battery charge limiter (80%% cap)")
    sp.add_argument("state", choices=["on", "off"])
    sp.set_defaults(func=cmd_limiter)

    sp = sub.add_parser("curves", help="show fan curves")
    sp.set_defaults(func=cmd_curves)

    sp = sub.add_parser("set-curve", help='set a fan curve, e.g. '
                        'frostctl set-curve cpu "40:20,60:45,80:80,93:100"')
    sp.add_argument("which", choices=["cpu", "gpu"])
    sp.add_argument("points", help="comma separated TEMP:PCT pairs")
    sp.set_defaults(func=cmd_set_curve)

    sp = sub.add_parser("watch", help="live one-line status (Ctrl-C to quit)")
    sp.set_defaults(func=cmd_watch)
    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except fb.DaemonError as exc:
        print(f"frostctl: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

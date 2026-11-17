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
frostbyte-daemon - root service that owns all hardware interaction.

Talks to the acer-wmi kernel driver (acer-wmi-linuwu / linuwu_sense layout)
through sysfs, polls sensors, runs the fan-curve engine and safety override,
and serves a small JSON API over a Unix socket for the CLI and GUI.

Protocol: one JSON object per line, request -> response.
    -> {"cmd": "get_status"}
    <- {"ok": true, "status": {...}}
"""

import configparser
import glob
import json
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frostbyte_common import (  # noqa: E402
    FROSTBYTE_VERSION, SOCKET_PATH, CONFIG_PATH, LOG_PATH,
    MODE_AUTO, MODE_MANUAL, MODE_MAX, MODE_CURVE, ALL_MODES,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SENSE_BASE_CANDIDATES = (
    "/sys/devices/platform/acer-wmi/nitro_sense",
    "/sys/devices/platform/acer-wmi/predator_sense",
)
HWMON_ROOT_CANDIDATES = (
    "/sys/devices/platform/acer-wmi/hwmon",
    "/sys/module/linuwu_sense/drivers/platform:acer-wmi/acer-wmi/hwmon",
)
PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
PLATFORM_PROFILE_CHOICES = "/sys/firmware/acpi/platform_profile_choices"

TICK_SECONDS = 2.0
CPU_TEMP_CRIT = 95       # degC: default crit threshold (configurable, [safety])
GPU_TEMP_CRIT = 88
CRIT_RELEASE_HYST = 8     # release override when below crit minus this
CRIT_SUSTAIN_TICKS = 3   # consecutive polls above crit before fans go 100%

DEFAULT_CPU_CURVE = [(40, 20), (55, 30), (65, 45), (75, 65), (85, 85), (93, 100)]
DEFAULT_GPU_CURVE = [(40, 20), (55, 35), (65, 55), (75, 75), (85, 100)]

log = logging.getLogger("frostbyte")


# ---------------------------------------------------------------------------
# Hardware interface
# ---------------------------------------------------------------------------

class Hardware:
    """Thin wrapper around the acer-wmi sysfs interface."""

    def __init__(self):
        self.sense_path = None
        self.hwmon_dir = None
        self.features = set()
        self.driver_version = None
        self.driver_name = None
        self.detect()

    def detect(self) -> bool:
        """(Re)locate the sysfs interface. Safe to call repeatedly."""
        for cand in SENSE_BASE_CANDIDATES:
            if os.path.isdir(cand):
                self.sense_path = cand
                break
        else:
            self.sense_path = None

        self.features = set()
        self.driver_version = None
        self.driver_name = None
        self.hwmon_dir = None

        if self.sense_path:
            for name in os.listdir(self.sense_path):
                if os.path.isfile(os.path.join(self.sense_path, name)):
                    self.features.add(name)
            self.driver_version = self._read_file(
                os.path.join(self.sense_path, "version"))
            self.driver_name = "acer-wmi-linuwu"
            log.info("sense interface found at %s (features: %s)",
                     self.sense_path, ", ".join(sorted(self.features)))

        for root in HWMON_ROOT_CANDIDATES:
            dirs = glob.glob(os.path.join(root, "hwmon*"))
            if dirs:
                self.hwmon_dir = dirs[0]
                break

        return self.sense_path is not None

    @property
    def available(self) -> bool:
        return self.sense_path is not None

    # -- low level helpers -------------------------------------------------

    @staticmethod
    def _read_file(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return None

    @staticmethod
    def _write_file(path, value) -> bool:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(str(value))
            return True
        except OSError as exc:
            log.error("write %s <- %r failed: %s", path, value, exc)
            return False

    def _sense_write(self, attr, value) -> bool:
        if not self.sense_path:
            return False
        return self._write_file(os.path.join(self.sense_path, attr), value)

    def _sense_read(self, attr):
        if not self.sense_path:
            return None
        return self._read_file(os.path.join(self.sense_path, attr))

    # -- fan control ---------------------------------------------------------

    def set_fan_speed(self, cpu: int, gpu: int) -> bool:
        cpu = max(0, min(100, int(cpu)))
        gpu = max(0, min(100, int(gpu)))
        ok = self._sense_write("fan_speed", f"{cpu},{gpu}")
        if ok:
            log.info("fan_speed set to %d,%d", cpu, gpu)
        return ok

    def get_fan_speed(self):
        raw = self._sense_read("fan_speed")
        if raw and "," in raw:
            try:
                a, b = raw.split(",")[:2]
                return int(a), int(b)
            except ValueError:
                pass
        return None

    # -- sensors -------------------------------------------------------------

    def fan_rpms(self):
        """Return (cpu_rpm, gpu_rpm), each possibly None."""
        cpu = gpu = None
        if self.hwmon_dir and os.path.isdir(self.hwmon_dir):
            for path in sorted(glob.glob(os.path.join(self.hwmon_dir, "fan*_input"))):
                idx = os.path.basename(path)[3]
                label = self._read_file(
                    os.path.join(self.hwmon_dir, f"fan{idx}_label")) or ""
                val = self._read_file(path)
                try:
                    rpm = int(val) if val is not None else None
                except ValueError:
                    rpm = None
                if "cpu" in label.lower() or idx == "1":
                    cpu = rpm if cpu is None else cpu
                elif "gpu" in label.lower() or idx == "2":
                    gpu = rpm if gpu is None else gpu
        return cpu, gpu

    @staticmethod
    def cpu_temp():
        """Package temperature from coretemp (degC, float or None)."""
        for d in glob.glob("/sys/class/hwmon/hwmon*"):
            if Hardware._read_file(os.path.join(d, "name")) == "coretemp":
                val = Hardware._read_file(os.path.join(d, "temp1_input"))
                if val:
                    try:
                        return int(val) / 1000.0
                    except ValueError:
                        return None
        return None

    @staticmethod
    def gpu_temp():
        """NVIDIA GPU temperature via nvidia-smi (degC, float or None)."""
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3)
            if out.returncode == 0:
                return float(out.stdout.strip().splitlines()[0])
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
        return None

    # -- platform profile ----------------------------------------------------

    @staticmethod
    def profile_choices():
        raw = Hardware._read_file(PLATFORM_PROFILE_CHOICES)
        return raw.split() if raw else []

    @staticmethod
    def get_profile():
        return Hardware._read_file(PLATFORM_PROFILE)

    @staticmethod
    def set_profile(name) -> bool:
        if name not in Hardware.profile_choices():
            log.warning("refusing unknown platform profile %r", name)
            return False
        ok = Hardware._write_file(PLATFORM_PROFILE, name)
        if ok:
            log.info("platform profile set to %s", name)
        return ok

    # -- misc sense attributes -------------------------------------------------

    def get_bool_attr(self, attr):
        raw = self._sense_read(attr)
        return None if raw is None else raw == "1"

    def set_bool_attr(self, attr, enabled: bool) -> bool:
        return self._sense_write(attr, "1" if enabled else "0")

    # -- power source ----------------------------------------------------------

    @staticmethod
    def ac_online():
        for path in glob.glob("/sys/class/power_supply/*/type"):
            if Hardware._read_file(path) == "Mains":
                val = Hardware._read_file(
                    os.path.join(os.path.dirname(path), "online"))
                return val == "1"
        return None


# ---------------------------------------------------------------------------
# Fan curve engine
# ---------------------------------------------------------------------------

def evaluate_curve(points, temp):
    """Linear interpolation over sorted [(temp, pct), ...]."""
    if temp is None or len(points) < 2:
        return None
    pts = sorted(points)
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            if t1 == t0:
                return p1
            ratio = (temp - t0) / (t1 - t0)
            return round(p0 + ratio * (p1 - p0))
    return pts[-1][1]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class Config:
    def __init__(self):
        self.mode = MODE_AUTO
        self.manual_cpu = 50
        self.manual_gpu = 50
        self.cpu_curve = list(DEFAULT_CPU_CURVE)
        self.gpu_curve = list(DEFAULT_GPU_CURVE)
        self.profile_ac = "performance"
        self.profile_battery = "balanced"
        self.follow_power_source = True
        self.cpu_crit = CPU_TEMP_CRIT
        self.gpu_crit = GPU_TEMP_CRIT

    @staticmethod
    def _parse_points(raw, fallback):
        pts = []
        for chunk in str(raw).split(","):
            chunk = chunk.strip()
            if not chunk or ":" not in chunk:
                continue
            t, p = chunk.split(":", 1)
            try:
                pts.append((int(float(t)), int(float(p))))
            except ValueError:
                continue
        pts = [(t, max(0, min(100, p))) for t, p in pts if 0 <= t <= 120]
        pts.sort()
        return pts if len(pts) >= 2 else list(fallback)

    @classmethod
    def load(cls):
        cfg = cls()
        if not os.path.exists(CONFIG_PATH):
            return cfg
        parser = configparser.ConfigParser()
        try:
            parser.read(CONFIG_PATH)
        except configparser.Error as exc:
            log.error("cannot parse %s: %s (using defaults)", CONFIG_PATH, exc)
            return cfg
        if parser.has_option("fans", "mode"):
            mode = parser.get("fans", "mode").strip().lower()
            if mode in ALL_MODES:
                cfg.mode = mode
        if parser.has_option("fans", "manual_cpu"):
            cfg.manual_cpu = parser.getint("fans", "manual_cpu",
                                           fallback=cfg.manual_cpu)
        if parser.has_option("fans", "manual_gpu"):
            cfg.manual_gpu = parser.getint("fans", "manual_gpu",
                                           fallback=cfg.manual_gpu)
        if parser.has_option("curves", "cpu"):
            cfg.cpu_curve = cls._parse_points(
                parser.get("curves", "cpu"), DEFAULT_CPU_CURVE)
        if parser.has_option("curves", "gpu"):
            cfg.gpu_curve = cls._parse_points(
                parser.get("curves", "gpu"), DEFAULT_GPU_CURVE)
        if parser.has_option("profiles", "ac"):
            cfg.profile_ac = parser.get("profiles", "ac").strip()
        if parser.has_option("profiles", "battery"):
            cfg.profile_battery = parser.get("profiles", "battery").strip()
        if parser.has_option("profiles", "follow_power_source"):
            cfg.follow_power_source = parser.getboolean(
                "profiles", "follow_power_source", fallback=True)
        if parser.has_option("safety", "cpu_crit"):
            cfg.cpu_crit = max(85, min(105, parser.getint(
                "safety", "cpu_crit", fallback=CPU_TEMP_CRIT)))
        if parser.has_option("safety", "gpu_crit"):
            cfg.gpu_crit = max(75, min(100, parser.getint(
                "safety", "gpu_crit", fallback=GPU_TEMP_CRIT)))
        return cfg

    def save(self):
        parser = configparser.ConfigParser()
        parser["fans"] = {
            "mode": self.mode,
            "manual_cpu": str(self.manual_cpu),
            "manual_gpu": str(self.manual_gpu),
        }
        parser["curves"] = {
            "cpu": ",".join(f"{t}:{p}" for t, p in self.cpu_curve),
            "gpu": ",".join(f"{t}:{p}" for t, p in self.gpu_curve),
        }
        parser["profiles"] = {
            "ac": self.profile_ac,
            "battery": self.profile_battery,
            "follow_power_source": "true" if self.follow_power_source else "false",
        }
        parser["safety"] = {
            "cpu_crit": str(self.cpu_crit),
            "gpu_crit": str(self.gpu_crit),
        }
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            parser.write(fh)
        os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class FrostByteDaemon:
    def __init__(self):
        self.hw = Hardware()
        self.config = Config.load()
        self.mode = self.config.mode
        self.lock = threading.Lock()
        self.running = True
        self.safety_override = False
        self._crit_streak = 0           # consecutive polls above crit temps
        self.last_applied = None        # last (cpu,gpu) actually written
        self.last_ac = None             # last observed AC state
        self.server_sock = None
        self._retry_counter = 0

    # -- control application --------------------------------------------------

    def _apply_fans(self, cpu, gpu):
        if self.last_applied == (cpu, gpu):
            return
        if self.hw.set_fan_speed(cpu, gpu):
            self.last_applied = (cpu, gpu)

    def apply_mode(self):
        """Push the current mode's fan setting to the hardware."""
        if not self.hw.available:
            return
        if self.safety_override:
            self._apply_fans(100, 100)
            return
        if self.mode == MODE_AUTO:
            self._apply_fans(0, 0)
        elif self.mode == MODE_MAX:
            self._apply_fans(100, 100)
        elif self.mode == MODE_MANUAL:
            self._apply_fans(self.config.manual_cpu, self.config.manual_gpu)
        # MODE_CURVE is applied per-tick in tick()

    # -- main loop --------------------------------------------------------------

    def tick(self):
        if not self.hw.available:
            self._retry_counter += 1
            if self._retry_counter % 5 == 1:
                log.warning("acer-wmi sense interface missing, retrying detection")
            self.hw.detect()
            if self.hw.available:
                self.apply_mode()
            return

        cpu_temp = self.hw.cpu_temp()
        gpu_temp = self.hw.gpu_temp()

        # Safety override: never let the machine cook because of a userspace
        # bug. Requires SUSTAINED heat (CRIT_SUSTAIN_TICKS polls) so brief
        # turbo transients don't slam the fans - the CPU's own thermal
        # throttling safely absorbs those 1-2s spikes.
        cpu_crit, gpu_crit = self.config.cpu_crit, self.config.gpu_crit
        crit_now = ((cpu_temp is not None and cpu_temp >= cpu_crit) or
                    (gpu_temp is not None and gpu_temp >= gpu_crit))
        self._crit_streak = self._crit_streak + 1 if crit_now else 0
        if self._crit_streak >= CRIT_SUSTAIN_TICKS and not self.safety_override:
            self.safety_override = True
            log.warning("SAFETY OVERRIDE: cpu=%s gpu=%s sustained %d polls - "
                        "forcing fans to 100%%",
                        cpu_temp, gpu_temp, self._crit_streak)
            self._apply_fans(100, 100)
        elif self.safety_override:
            cool_cpu = cpu_temp is None or cpu_temp < cpu_crit - CRIT_RELEASE_HYST
            cool_gpu = gpu_temp is None or gpu_temp < gpu_crit - CRIT_RELEASE_HYST
            if cool_cpu and cool_gpu:
                self.safety_override = False
                self._crit_streak = 0
                self.last_applied = None
                log.info("temperatures back to safe levels, releasing override")
                self.apply_mode()

        if not self.safety_override and self.mode == MODE_CURVE:
            cpu_target = evaluate_curve(self.config.cpu_curve, cpu_temp)
            gpu_target = evaluate_curve(self.config.gpu_curve, gpu_temp)
            cur = self.last_applied or self.hw.get_fan_speed() or (None, None)
            new_cpu = cpu_target if cpu_target is not None else cur[0]
            new_gpu = gpu_target if gpu_target is not None else cur[1]
            if new_cpu is not None and new_gpu is not None:
                self._apply_fans(int(new_cpu), int(new_gpu))

        # AC/battery profile memory
        ac = self.hw.ac_online()
        if ac is not None and ac != self.last_ac:
            self.last_ac = ac
            if self.config.follow_power_source:
                want = (self.config.profile_ac if ac
                        else self.config.profile_battery)
                if want and want in self.hw.profile_choices():
                    self.hw.set_profile(want)

    # -- status ------------------------------------------------------------------

    def status(self):
        cpu_temp = self.hw.cpu_temp()
        gpu_temp = self.hw.gpu_temp()
        cpu_rpm, gpu_rpm = self.hw.fan_rpms()
        setting = self.hw.get_fan_speed() or [None, None]
        return {
            "daemon_version": FROSTBYTE_VERSION,
            "driver_present": self.hw.available,
            "driver_name": self.hw.driver_name,
            "driver_version": self.hw.driver_version,
            "sense_path": self.hw.sense_path,
            "features": sorted(self.hw.features),
            "cpu_temp": cpu_temp,
            "gpu_temp": gpu_temp,
            "cpu_rpm": cpu_rpm,
            "gpu_rpm": gpu_rpm,
            "fan_setting": setting,
            "mode": self.mode,
            "safety_override": self.safety_override,
            "profile": self.hw.get_profile(),
            "profile_choices": self.hw.profile_choices(),
            "ac_online": self.hw.ac_online(),
            "battery_limiter": (self.hw.get_bool_attr("battery_limiter")
                                if "battery_limiter" in self.hw.features else None),
            "backlight_timeout": (self.hw.get_bool_attr("backlight_timeout")
                                  if "backlight_timeout" in self.hw.features else None),
            "config": {
                "manual_cpu": self.config.manual_cpu,
                "manual_gpu": self.config.manual_gpu,
                "cpu_curve": [list(p) for p in self.config.cpu_curve],
                "gpu_curve": [list(p) for p in self.config.gpu_curve],
                "profile_ac": self.config.profile_ac,
                "profile_battery": self.config.profile_battery,
                "follow_power_source": self.config.follow_power_source,
                "cpu_crit": self.config.cpu_crit,
                "gpu_crit": self.config.gpu_crit,
            },
        }

    # -- command dispatch ----------------------------------------------------------

    def dispatch(self, req):
        cmd = req.get("cmd")
        if cmd == "ping":
            return {"ok": True, "version": FROSTBYTE_VERSION}
        if cmd == "get_status":
            return {"ok": True, "status": self.status()}

        with self.lock:
            if cmd == "set_mode":
                mode = str(req.get("mode", "")).lower()
                if mode not in ALL_MODES:
                    return {"ok": False, "error": f"bad mode {mode!r}"}
                self.mode = mode
                self.config.mode = mode
                self.apply_mode()
                self.config.save()
                return {"ok": True, "mode": self.mode}

            if cmd == "set_fans":
                try:
                    cpu = int(req["cpu"])
                    gpu = int(req["gpu"])
                except (KeyError, TypeError, ValueError):
                    return {"ok": False, "error": "need integer cpu and gpu (0-100)"}
                if not (0 <= cpu <= 100 and 0 <= gpu <= 100):
                    return {"ok": False, "error": "fan speeds must be 0-100"}
                self.mode = MODE_MANUAL
                self.config.mode = MODE_MANUAL
                self.config.manual_cpu = cpu
                self.config.manual_gpu = gpu
                self.safety_override = False
                self._apply_fans(cpu, gpu)
                self.config.save()
                return {"ok": True, "mode": self.mode}

            if cmd == "set_profile":
                profile = str(req.get("profile", "")).strip()
                if not self.hw.set_profile(profile):
                    return {"ok": False, "error": f"profile {profile!r} rejected"}
                ac = self.hw.ac_online()
                if ac is True:
                    self.config.profile_ac = profile
                elif ac is False:
                    self.config.profile_battery = profile
                self.config.save()
                return {"ok": True}

            if cmd == "set_limiter":
                if "battery_limiter" not in self.hw.features:
                    return {"ok": False, "error": "battery_limiter unsupported"}
                ok = self.hw.set_bool_attr("battery_limiter",
                                           bool(req.get("enabled")))
                return {"ok": ok}

            if cmd == "set_backlight_timeout":
                if "backlight_timeout" not in self.hw.features:
                    return {"ok": False, "error": "backlight_timeout unsupported"}
                ok = self.hw.set_bool_attr("backlight_timeout",
                                           bool(req.get("enabled")))
                return {"ok": ok}

            if cmd == "set_follow_power":
                self.config.follow_power_source = bool(req.get("enabled"))
                self.config.save()
                return {"ok": True}

            if cmd == "set_crit":
                try:
                    cpu = int(req.get("cpu"))
                    gpu = int(req.get("gpu"))
                except (TypeError, ValueError):
                    return {"ok": False, "error": "need integer cpu/gpu"}
                if not (85 <= cpu <= 105) or not (75 <= gpu <= 100):
                    return {"ok": False,
                            "error": "range: cpu 85-105, gpu 75-100"}
                self.config.cpu_crit = cpu
                self.config.gpu_crit = gpu
                self.config.save()
                return {"ok": True}

            if cmd == "set_curve":
                which = str(req.get("which", "")).lower()
                points = req.get("points")
                if which not in ("cpu", "gpu") or not isinstance(points, list):
                    return {"ok": False, "error": "need which=cpu|gpu and points"}
                fallback = (DEFAULT_CPU_CURVE if which == "cpu"
                            else DEFAULT_GPU_CURVE)
                parsed = Config._parse_points(
                    ",".join(f"{t}:{p}" for t, p in points), fallback)
                if which == "cpu":
                    self.config.cpu_curve = parsed
                else:
                    self.config.gpu_curve = parsed
                self.config.save()
                return {"ok": True}

            if cmd == "save_config":
                self.config.save()
                return {"ok": True}

        return {"ok": False, "error": f"unknown command {cmd!r}"}

    # -- socket server ---------------------------------------------------------

    def _client_thread(self, conn):
        try:
            conn.settimeout(5)
            data = b""
            while b"\n" not in data and len(data) < 65536:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            req = json.loads(data.decode().strip() or "{}")
            reply = self.dispatch(req)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            reply = {"ok": False, "error": f"bad request: {exc}"}
        except Exception as exc:  # never let a client crash the daemon
            log.exception("dispatch error")
            reply = {"ok": False, "error": f"internal error: {exc}"}
        try:
            conn.sendall((json.dumps(reply) + "\n").encode())
        except OSError:
            pass
        finally:
            conn.close()

    def serve(self):
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o666)  # local single-user machine; same model as DAMX
        self.server_sock.listen(8)
        log.info("listening on %s", SOCKET_PATH)
        while self.running:
            try:
                conn, _ = self.server_sock.accept()
            except OSError:
                break
            threading.Thread(target=self._client_thread, args=(conn,),
                             daemon=True).start()

    # -- lifecycle ------------------------------------------------------------

    def shutdown(self, *_):
        log.info("shutting down - returning fans to automatic control")
        self.running = False
        try:
            if self.hw.available:
                self.hw.set_fan_speed(0, 0)
            self.config.save()
        except Exception:
            log.exception("error during shutdown")
        if self.server_sock:
            try:
                self.server_sock.close()
            except OSError:
                pass
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass
        os._exit(0)

    def run(self):
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        server = threading.Thread(target=self.serve, daemon=True)
        server.start()
        log.info("FrostByte daemon %s started (mode=%s)",
                 FROSTBYTE_VERSION, self.mode)
        self.apply_mode()
        while self.running:
            try:
                self.tick()
            except Exception:
                log.exception("tick error")
            time.sleep(TICK_SECONDS)


def main():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=2))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    if os.geteuid() != 0:
        log.error("frostbyte-daemon must run as root")
        sys.exit(1)
    FrostByteDaemon().run()


if __name__ == "__main__":
    main()

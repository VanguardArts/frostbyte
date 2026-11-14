# Contributing to FrostByte

FrostByte is free software (GPL-3.0-or-later) and contributions are welcome.

## Reporting bugs

Include: laptop model (`cat /sys/class/dmi/id/product_name`), kernel
(`uname -r`), `dkms status`, `frostctl status` output, and
`journalctl -u frostbyte-daemon` logs.

## Hacking

No build step — it's Python + a DKMS kernel driver.

- Daemon: `daemon/frostbyte-daemon.py` (stdlib only, keep it that way)
- CLI: `cli/frostctl.py`
- GUI: `gui/frostbyte_gui.py` (PyQt6)
- Driver: `driver/acer-wmi-linuwu/` is **vendored** — prefer fixing driver
  bugs upstream, then re-vendor.

Test changes against the real daemon:
`sudo systemctl restart frostbyte-daemon && frostctl status`.

## Ground rules

- Never bypass the safety override; cooling failures destroy hardware.
- The daemon must stay dependency-free (Python stdlib).
- GUI/CLI must never touch sysfs directly — always via the daemon socket.

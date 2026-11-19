"""FrostByte GPU helpers - NVIDIA dGPU detection, status, and PRIME offload.

Lightweight, dependency-free wrappers around ``nvidia-smi`` so the GUI can show
the dedicated GPU's live state and launch apps on it. Designed to degrade
gracefully: every function returns ``None``/``[]`` when no NVIDIA GPU is
present or ``nvidia-smi`` is unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# Ordered nvidia-smi --query-gpu fields we read for the live dashboard.
# Keys map directly to the CSV columns (all "noheader,nounits").
GPU_FIELDS = (
    "name",
    "temperature.gpu",
    "clocks.sm",
    "clocks.mem",
    "power.draw",
    "power.limit",
    "utilization.gpu",
    "memory.used",
    "memory.total",
)


def nvidia_smi() -> str | None:
    """Absolute path to nvidia-smi, or None if not installed."""
    return shutil.which("nvidia-smi")


def gpu_available() -> bool:
    return nvidia_smi() is not None


def _run(args: list[str], timeout: float = 3.0) -> str:
    """Run nvidia-smi and return stdout as text; empty on any failure."""
    exe = nvidia_smi()
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout


def gpu_status() -> dict | None:
    """Live dGPU snapshot keyed by friendly names, or None if unavailable.

    Values are kept as the raw nvidia-smi strings ("N/A" when unsupported);
    the GUI is responsible for pretty-printing. ``None`` means no dGPU.
    """
    raw = _run(["--query-gpu=" + ",".join(GPU_FIELDS),
                "--format=csv,noheader,nounits"])
    if not raw.strip():
        return None
    values = [v.strip() for v in raw.strip().split(",")]
    if len(values) < len(GPU_FIELDS):
        return None
    return dict(zip(GPU_FIELDS, values))


def gpu_processes() -> list[dict]:
    """Processes currently running on the dGPU (CUDA/compute).

    Each entry is ``{"pid", "name", "mem"}``. PRIME-rendered graphics apps
    are reported by nvidia-smi under a separate table; compute apps (CUDA,
    e.g. llama.cpp/Bionic) are the common case on this machine.
    """
    raw = _run(["--query-compute-apps=pid,process_name,used_gpu_memory",
                "--format=csv,noheader,nounits"])
    procs = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0]:
            procs.append({
                "pid": parts[0],
                "name": parts[1],
                "mem": parts[2] if len(parts) > 2 else "",
            })
    return procs


def launch_on_gpu(command: list[str], cwd: str | None = None):
    """Launch ``command`` on the NVIDIA dGPU via PRIME render offload.

    Returns the ``subprocess.Popen`` handle. The child is started in its own
    session so it survives FrostByte exiting the foreground.
    """
    base = dict(os.environ)
    base.update({
        "__NV_PRIME_RENDER_OFFLOAD": "1",
        "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
        "__VK_LAYER_NV_optimus": "NVIDIA_only",
    })
    return subprocess.Popen(
        command,
        env=base,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

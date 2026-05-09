"""Host capability snapshot for the chat boot banner.

Stays cross-platform with stdlib only — no psutil dependency. Disk and
memory readings are best-effort: if the platform-specific path fails,
the field is empty and the banner just omits it.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
from dataclasses import dataclass

__all__ = ["SystemInfo", "collect_system_info"]


@dataclass(frozen=True)
class SystemInfo:
    os_name: str
    os_release: str
    hostname: str
    cpu: str
    cpu_count: int
    ram_total_gb: float       # 0.0 when unknown
    ram_free_gb: float        # 0.0 when unknown
    disk_total_gb: float      # workspace partition (or cwd)
    disk_free_gb: float


def _read_meminfo() -> tuple[float, float]:
    """POSIX-Linux only. Returns (total_gb, free_gb) or (0, 0)."""
    try:
        info = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, val = line.partition(":")
                parts = val.strip().split()
                if not parts:
                    continue
                # All values are kB.
                info[key.strip()] = int(parts[0])
        total = info.get("MemTotal", 0) / (1024 ** 2)
        free = (info.get("MemAvailable") or info.get("MemFree", 0)) / (1024 ** 2)
        return total, free
    except (OSError, ValueError):
        return 0.0, 0.0


def _read_macos_mem() -> tuple[float, float]:
    try:
        import subprocess
        total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()) / (1024 ** 3)
        # vm_stat output is in 4096-byte pages; quick parse.
        out = subprocess.check_output(["vm_stat"]).decode()
        free_pages = 0
        for line in out.splitlines():
            if line.startswith("Pages free"):
                free_pages = int(line.split(":")[1].strip().rstrip("."))
                break
        free = free_pages * 4096 / (1024 ** 3)
        return total, free
    except Exception:
        return 0.0, 0.0


def _read_windows_mem() -> tuple[float, float]:
    try:
        import ctypes
        class MEMSTAT(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]
        s = MEMSTAT()
        s.dwLength = ctypes.sizeof(s)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return s.ullTotalPhys / (1024 ** 3), s.ullAvailPhys / (1024 ** 3)
    except Exception:
        return 0.0, 0.0


def _disk_usage(path: str) -> tuple[float, float]:
    try:
        usage = shutil.disk_usage(path)
        return usage.total / (1024 ** 3), usage.free / (1024 ** 3)
    except OSError:
        return 0.0, 0.0


def _cpu_brand() -> str:
    try:
        if platform.system() == "Linux":
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        elif platform.system() == "Darwin":
            import subprocess
            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
            ).decode().strip()
        elif platform.system() == "Windows":
            import subprocess
            out = subprocess.check_output(
                ["wmic", "cpu", "get", "Name"], text=True,
            )
            lines = [l.strip() for l in out.splitlines() if l.strip() and "Name" not in l]
            if lines:
                return lines[0]
    except Exception:
        pass
    return platform.processor() or "unknown CPU"


def collect_system_info(workspace: str = "") -> SystemInfo:
    """Best-effort host snapshot. ``workspace`` picks which disk to probe."""
    if platform.system() == "Linux":
        total, free = _read_meminfo()
    elif platform.system() == "Darwin":
        total, free = _read_macos_mem()
    elif platform.system() == "Windows":
        total, free = _read_windows_mem()
    else:
        total, free = 0.0, 0.0

    disk_path = workspace or os.path.expanduser("~")
    if not os.path.exists(disk_path):
        disk_path = os.getcwd()
    disk_total, disk_free = _disk_usage(disk_path)

    return SystemInfo(
        os_name=platform.system(),
        os_release=platform.release(),
        hostname=socket.gethostname(),
        cpu=_cpu_brand(),
        cpu_count=os.cpu_count() or 1,
        ram_total_gb=round(total, 1),
        ram_free_gb=round(free, 1),
        disk_total_gb=round(disk_total, 1),
        disk_free_gb=round(disk_free, 1),
    )

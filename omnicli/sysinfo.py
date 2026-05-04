"""
PhantomCLI — System Information Detection
Detects OS, hardware, and calculates optimal parallel agent count.
Runs once on first boot, saved to DB, used in every system prompt.
"""
import os
import platform
import subprocess


def detect_system() -> dict:
    info: dict = {}

    info["os"]       = platform.system()          # Linux | Darwin | Windows
    info["arch"]     = platform.machine()          # x86_64 | arm64
    info["hostname"] = platform.node()
    info["python"]   = platform.python_version()

    # ── Distro / OS version ───────────────────────────────────────────────────
    if info["os"] == "Linux":
        info["distro"] = _linux_distro()
    elif info["os"] == "Darwin":
        info["distro"] = f"macOS {platform.mac_ver()[0]}"
    else:
        info["distro"] = _windows_friendly_name()

    # ── CPU ───────────────────────────────────────────────────────────────────
    info["cpu_cores"] = os.cpu_count() or 2
    info["cpu_model"] = _cpu_model(info["os"])

    # ── RAM ───────────────────────────────────────────────────────────────────
    info["ram_gb"] = _ram_gb(info["os"])

    # ── GPU ───────────────────────────────────────────────────────────────────
    info["gpu"] = _gpu_name()

    # ── Disk (home partition — cross-platform via shutil) ────────────────────
    try:
        import shutil
        _check_path = os.path.expanduser("~")
        du = shutil.disk_usage(_check_path)
        info["disk_free_gb"]  = round(du.free  / 1024**3, 1)
        info["disk_total_gb"] = round(du.total / 1024**3, 1)
    except Exception:
        info["disk_free_gb"] = 0
        info["disk_total_gb"] = 0

    # ── Max parallel agents ───────────────────────────────────────────────────
    info["max_agents"] = _calc_max_agents(info)

    return info


# ── Internal helpers ──────────────────────────────────────────────────────────

def _linux_distro() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    try:
        r = subprocess.run(["lsb_release", "-d", "-s"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "Linux"


def _windows_friendly_name() -> str:
    """Returns 'Windows 11' / 'Windows 10' / etc. — Win11 has same major/minor as Win10
    but build >= 22000. platform.version() gives e.g. '10.0.26200'."""
    try:
        ver = platform.version()  # e.g. "10.0.26200"
        parts = ver.split(".")
        if len(parts) >= 3 and parts[0] == "10":
            build = int(parts[2])
            if build >= 22000:
                return f"Windows 11 (build {build})"
            return f"Windows 10 (build {build})"
        return f"Windows {ver}"
    except Exception:
        return "Windows"


def _cpu_model(os_name: str) -> str:
    try:
        if os_name == "Linux":
            r = subprocess.run(["grep", "-m", "1", "model name", "/proc/cpuinfo"],
                               capture_output=True, text=True, timeout=3)
            if ":" in r.stdout:
                return r.stdout.split(":", 1)[1].strip()
        elif os_name == "Darwin":
            r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                               capture_output=True, text=True, timeout=3)
            if r.stdout.strip():
                return r.stdout.strip()
        elif os_name == "Windows":
            # Try PowerShell first (wmic deprecated/removed on Win11)
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_Processor).Name"],
                    capture_output=True, text=True, timeout=8,
                )
                name = (r.stdout or "").strip().splitlines()[0].strip() if r.stdout.strip() else ""
                if name:
                    return name[:80]
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ["wmic", "cpu", "get", "name"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if line and line.lower() != "name":
                        return line[:80]
            except Exception:
                pass
        return platform.processor() or "Unknown CPU"
    except Exception:
        return platform.processor() or "Unknown CPU"


def _ram_gb(os_name: str) -> float:
    try:
        if os_name == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / 1024 / 1024, 1)
        elif os_name == "Darwin":
            r = subprocess.run(["sysctl", "-n", "hw.memsize"],
                               capture_output=True, text=True, timeout=3)
            return round(int(r.stdout.strip()) / 1024**3, 1)
        elif os_name == "Windows":
            # PowerShell first (wmic deprecated on Win11)
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
                    capture_output=True, text=True, timeout=8,
                )
                txt = (r.stdout or "").strip().splitlines()
                if txt and txt[0].strip().isdigit():
                    return round(int(txt[0].strip()) / 1024**3, 1)
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ["wmic", "computersystem", "get", "TotalPhysicalMemory"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        return round(int(line) / 1024**3, 1)
            except Exception:
                pass
    except Exception:
        pass
    # Universal fallback via psutil (optional dep) or raw estimate
    try:
        import psutil
        return round(psutil.virtual_memory().total / 1024**3, 1)
    except ImportError:
        pass
    return 4.0


def live_ram(os_name: str) -> tuple:
    """Returns (free_gb, total_gb, pct_used) probed live every call. Never raises."""
    try:
        if os_name == "Linux":
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, _, rest = line.partition(":")
                    parts = rest.strip().split()
                    if parts:
                        mem[k] = int(parts[0])  # kB
            total = mem.get("MemTotal", 0)
            avail = mem.get("MemAvailable", mem.get("MemFree", 0))
            if total:
                return (round(avail / 1024 / 1024, 1),
                        round(total / 1024 / 1024, 1),
                        int((total - avail) / total * 100))
        elif os_name == "Windows":
            # PowerShell — works on Win10 and Win11
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "$o=Get-CimInstance Win32_OperatingSystem; "
                     "Write-Output \"$($o.FreePhysicalMemory) $($o.TotalVisibleMemorySize)\""],
                    capture_output=True, text=True, timeout=8,
                )
                parts = (r.stdout or "").strip().split()
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    free_kb, total_kb = int(parts[0]), int(parts[1])
                    return (round(free_kb / 1024 / 1024, 1),
                            round(total_kb / 1024 / 1024, 1),
                            int((total_kb - free_kb) / total_kb * 100) if total_kb else 0)
            except Exception:
                pass
        elif os_name == "Darwin":
            r = subprocess.run(["sysctl", "-n", "hw.memsize"],
                               capture_output=True, text=True, timeout=3)
            total_b = int(r.stdout.strip())
            r2 = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3)
            page = 4096
            free_pages = inactive_pages = 0
            for line in r2.stdout.splitlines():
                if "Pages free" in line:
                    free_pages = int(line.split(":")[1].strip().rstrip("."))
                elif "Pages inactive" in line:
                    inactive_pages = int(line.split(":")[1].strip().rstrip("."))
            free_b = (free_pages + inactive_pages) * page
            total_gb = round(total_b / 1024**3, 1)
            free_gb  = round(free_b  / 1024**3, 1)
            pct = int((total_b - free_b) / total_b * 100) if total_b else 0
            return (free_gb, total_gb, pct)
    except Exception:
        pass
    # psutil last resort
    try:
        import psutil
        vm = psutil.virtual_memory()
        return (round(vm.available / 1024**3, 1),
                round(vm.total / 1024**3, 1),
                int(vm.percent))
    except Exception:
        return (None, None, None)


def _gpu_name() -> str:
    # nvidia-smi works on Linux, Mac, and Windows if NVIDIA drivers are installed
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().split("\n")[0]
    except Exception:
        pass
    # macOS: system_profiler
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                if "Chipset Model:" in line:
                    return line.split(":", 1)[1].strip()[:50]
    except Exception:
        pass
    # Windows: PowerShell first, wmic fallback
    if platform.system() == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"],
                capture_output=True, text=True, timeout=8,
            )
            name = (r.stdout or "").strip().splitlines()
            if name and name[0].strip():
                return name[0].strip()[:60]
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip() and l.strip() != "Name"]
            if lines:
                return lines[0][:60]
        except Exception:
            pass
    # Linux fallback: lspci
    try:
        r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if "VGA" in line or "3D" in line:
                return line.split(":")[-1].strip()[:50]
    except Exception:
        pass
    return "None"


def _calc_max_agents(info: dict) -> int:
    ram    = info.get("ram_gb", 4)
    cores  = info.get("cpu_cores", 2)
    ram_limit  = 1 if ram < 4 else (2 if ram < 8 else (3 if ram < 16 else 4))
    core_limit = max(1, min(cores // 2, 4))
    return min(ram_limit, core_limit, 4)


def format_system_card(info: dict) -> list[tuple]:
    """Returns (label, value, colour) rows for HUD display."""
    from omnicli.tui import GRN, CY, AMB, DIM
    rows = [
        ("OS",         f"{info.get('distro', info.get('os', '?'))} ({info.get('arch', '?')})", CY),
        ("CPU",        f"{info.get('cpu_model', '?')} · {info.get('cpu_cores', '?')} cores",   CY),
        ("RAM",        f"{info.get('ram_gb', '?')} GB",                                         GRN),
        ("GPU",        info.get("gpu", "None"),                                                  CY),
        ("DISK",       f"{info.get('disk_free_gb', 0)} GB free / {info.get('disk_total_gb', 0)} GB total", DIM),
        ("PYTHON",     info.get("python", "?"),                                                  DIM),
        ("MAX AGENTS", f"{info.get('max_agents', 2)} parallel agents auto-configured",          GRN),
    ]
    return rows

"""
PhantomCLI Executor — secure bash execution
Industry-standard dangerous command blocking based on:
  - OWASP Command Injection prevention
  - CIS Benchmarks for Linux hardening
  - Common destructive patterns from security research
"""

import re
import json
import os
import subprocess
import time
import logging
from omnicli.memory import get_config

log = logging.getLogger("omnicli.executor")

# ─── GOD-MODE (TRUST 4) SESSION TTL ───────────────────────────────────────────
# God Mode is extremely powerful. It should not persist forever. We stamp a
# timestamp in config when the user activates Trust 4 and auto-downgrade to
# Trust 3 once the TTL elapses. The timestamp is also invalidated on every
# CLI restart (we set it on explicit activation, not on every boot), so a
# compromised shell session can't silently inherit God Mode.

_GOD_MODE_TTL_DEFAULT_S = 30 * 60    # 30 minutes


def _god_mode_ttl_s() -> int:
    try:
        return max(60, int(get_config("god_mode_ttl_s", str(_GOD_MODE_TTL_DEFAULT_S))
                           or _GOD_MODE_TTL_DEFAULT_S))
    except (TypeError, ValueError):
        return _GOD_MODE_TTL_DEFAULT_S


def mark_god_mode_activated() -> None:
    """Call this when the user explicitly activates Trust 4."""
    from omnicli.memory import save_config
    save_config("god_mode_activated_at", str(int(time.time())))
    log.info("god mode activated (TTL %ds)", _god_mode_ttl_s())


def god_mode_active() -> bool:
    """True if Trust 4 is within its session TTL. False otherwise.

    Re-checked on every call so the TTL is respected per-command, not
    per-session. If the stamp is present but expired, we eagerly clear it
    so subsequent calls don't keep logging 'downgrade' warnings forever.
    """
    try:
        raw = get_config("god_mode_activated_at", "")
        if not raw:
            return False
        elapsed = time.time() - int(raw)
        if elapsed <= _god_mode_ttl_s():
            return True
        # Expired — clear the stamp so the next call is a clean miss.
        try:
            from omnicli.memory import save_config as _sc
            _sc("god_mode_activated_at", "")
        except Exception:
            pass
        return False
    except (TypeError, ValueError):
        return False


def effective_trust(configured: int) -> int:
    """
    Translate the caller's configured trust level into the level that should
    actually be enforced right now. Auto-downgrades an expired God Mode.
    """
    if configured >= 4 and not god_mode_active():
        log.warning("god mode TTL expired — downgrading trust 4 → 3 for this command")
        return 3
    return configured

# ─── AUDIT LOG ────────────────────────────────────────────────────────────────

_AUDIT_LOG = os.path.expanduser("~/.omnicli/.audit.log")


def _audit(command: str, trust_level: int, allowed: bool, reason: str = "") -> None:
    """Append a tamper-evident entry to the audit log (owner-read only)."""
    try:
        entry = json.dumps({
            "ts":    time.time(),
            "cmd":   command[:500],
            "trust": trust_level,
            "ok":    allowed,
            "why":   reason,
        })
        with open(_AUDIT_LOG, "a") as f:
            f.write(entry + "\n")
        if os.name != "nt":   # chmod not supported on Windows
            os.chmod(_AUDIT_LOG, 0o600)
    except Exception:
        pass


# ─── PERMANENT BLOCKLIST ──────────────────────────────────────────────────────
# Matched against the FULL command string (case-insensitive, after normalization).
# These are NEVER executed regardless of trust level.

BLOCKED_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ── Filesystem destruction ──────────────────────────────────────────────
    # Matches: rm -rf /, rm -rf /home, rm -rf /var/log, etc.
    (re.compile(r'\brm\b.+(-[^\s]*r[^\s]*|-r|-rf|-fr).+/[a-zA-Z0-9_.*]*', re.I),
                                                                         "rm -rf on system path"),
    (re.compile(r'\brm\b.+(-[^\s]*r[^\s]*).*(/\*|~/?/?\*)', re.I),     "recursive deletion from root/home"),
    (re.compile(r'\bsudo\s+rm\b', re.I),                                 "sudo rm permanently blocked"),
    (re.compile(r'\bmkfs\b', re.I),                                      "filesystem format"),
    (re.compile(r'\bwipefs\b', re.I),                                    "filesystem signature wipe"),
    (re.compile(r'\b(fdisk|parted|gdisk)\b.*(-w|write)', re.I),         "interactive disk write"),

    # ── Block device destruction ────────────────────────────────────────────
    (re.compile(r'\bdd\b.*(of=/?/?dev/)', re.I),                        "dd to block device"),
    (re.compile(r'\bdd\b.*if=/?/?dev/zero.*of=', re.I),                 "zero-fill with dd"),
    (re.compile(r'\bshred\b.*(/?/?dev/|/?boot/)', re.I),                "shred on system/boot device"),

    # ── Fork bomb / resource exhaustion ────────────────────────────────────
    (re.compile(r':\s*\(\s*\)\s*\{.*:\s*\|.*:\s*&?\s*\}', re.I),       "fork bomb"),
    (re.compile(r'while\s+true.*fork|while\s+:\s*;.*&', re.I),         "infinite fork loop"),
    (re.compile(r'yes\s*\|\s*yes', re.I),                               "yes pipe bomb"),

    # ── Privilege escalation ────────────────────────────────────────────────
    (re.compile(r'\bchmod\b.+-[rR].+777\s+/', re.I),                    "recursive 777 on /"),
    (re.compile(r'\bchown\b.+-[rR].+(root|:0)\s+/', re.I),             "recursive chown root on /"),
    (re.compile(r'\bsudo\s+su\b', re.I),                                "sudo su escalation"),
    (re.compile(r'(echo|tee|>>)\s*.*/etc/sudoers', re.I),               "modifying sudoers"),
    (re.compile(r'>\s*/etc/passwd', re.I),                              "overwriting /etc/passwd"),
    (re.compile(r'>\s*/etc/shadow', re.I),                              "overwriting /etc/shadow"),

    # ── Shell evasion patterns ──────────────────────────────────────────────
    (re.compile(r'\beval\b.*(\$\(|`)', re.I),                          "eval with command substitution"),
    (re.compile(r'\bexec\b.*\brm\b', re.I),                            "exec rm"),
    (re.compile(r'((/usr)?/bin/)?(ba)?sh\s+-c\s+.*rm\b', re.I),       "shell -c rm (any path)"),
    (re.compile(r'\bbash\b.*-c\s+["\'].*rm\b', re.I),                  "bash -c 'rm ...'"),
    (re.compile(r'\bpython[23]?\b.*-c.*\bos\.system\b', re.I),         "python os.system execution"),
    (re.compile(r'\bperl\b.*-e.*system', re.I),                        "perl system execution"),

    # ── Network / firewall ──────────────────────────────────────────────────
    (re.compile(r'\biptables\s+-F\b', re.I),                            "flush iptables"),
    (re.compile(r'\bufw\b.+--force\s+reset', re.I),                    "reset UFW firewall"),
    (re.compile(r'\bnft\s+flush\s+ruleset', re.I),                     "flush nftables"),

    # ── System shutdown / halt ──────────────────────────────────────────────
    (re.compile(r'\b(shutdown|poweroff|halt|reboot)\b(?!\s*(-l|--help|--show))', re.I),
                                                                        "shutdown/reboot"),
    (re.compile(r'\binit\s+[06]\b', re.I),                             "init 0/6"),

    # ── Kernel / boot ──────────────────────────────────────────────────────
    (re.compile(r'\brm\b.+(/boot/|vmlinuz|initrd)', re.I),             "delete kernel/boot"),
    (re.compile(r'(echo|tee).*>.*/?proc/sys/', re.I),                  "write to /proc/sys"),
    (re.compile(r'\bsysctl\s+-w\b', re.I),                             "modify kernel params"),

    # ── Database destruction ────────────────────────────────────────────────
    (re.compile(r'\bDROP\s+(DATABASE|SCHEMA)\b', re.I),                "DROP DATABASE/SCHEMA"),
    (re.compile(r'\bDROP\s+TABLE\s+\*', re.I),                        "DROP all tables"),

    # ── Crypto / ransomware ─────────────────────────────────────────────────
    (re.compile(r'\bopenssl\b.*-e.*-pass.*\*\.\*', re.I),             "batch file encryption"),
    (re.compile(r'for\b.*in.*/?/?\*.*do.*openssl', re.I),              "recursive openssl loop"),

    # ── Reverse shells ─────────────────────────────────────────────────────
    (re.compile(r'(bash|sh|nc|ncat|python|perl|ruby)\s.*(-e|--exec).*(/bin/(ba)?sh|cmd\.exe)', re.I),
                                                                        "reverse shell"),
    (re.compile(r'\bnc\b.*-[elp]+.*\d+\s*-e\s*/bin', re.I),          "netcat reverse shell"),

    # ── Windows equivalents ─────────────────────────────────────────────────
    (re.compile(r'format\s+[a-zA-Z]:\s*/[qyQ]', re.I),                "Windows format drive"),
    (re.compile(r'rd\s+/[sS]\s+/[qQ]\s+[cCdD]:', re.I),              "Windows rmdir /s"),
    (re.compile(r'Stop-Computer|Restart-Computer', re.I),               "PowerShell shutdown"),
]

# ─── TRUST-GATED PATTERNS ─────────────────────────────────────────────────────

SENSITIVE_PATTERNS = [
    (re.compile(r'\bsudo\b', re.I),                                    "sudo requires Trust 3+"),
    (re.compile(r'\brm\b.*-[rRfF]', re.I),                            "recursive/force rm requires Trust 3+"),
    (re.compile(r'\bsystemctl\b.*(stop|disable|mask|kill)', re.I),    "stopping services requires Trust 3+"),
    (re.compile(r'\bapt(-get)?\b.*(remove|purge|autoremove)', re.I),  "removing packages requires Trust 3+"),
    (re.compile(r'\bpip\b.*(uninstall)', re.I),                       "pip uninstall requires Trust 3+"),
    (re.compile(r'\bcurl\b.*\|\s*(bash|sh|python3?)', re.I),          "pipe to shell requires Trust 3+"),
    (re.compile(r'\bwget\b.*-[Oq].*\|\s*(bash|sh)', re.I),           "pipe to shell requires Trust 3+"),
]

SAFE_PREFIXES = (
    "ls", "cat", "head", "tail", "pwd", "echo", "whoami", "id",
    "date", "uptime", "df", "du", "free", "ps", "top", "htop",
    "grep", "find", "which", "type", "file", "stat", "wc",
    "curl -s", "curl --silent", "ping -c", "traceroute",
    "git log", "git status", "git diff", "git branch",
    "python --version", "python3 --version", "node --version",
    "pip list", "pip show", "npm list",
    "systemctl status", "journalctl -n",
    "netstat", "ss -", "lsof",
    "dir", "ipconfig", "Get-",
)


# ─── NORMALIZATION ────────────────────────────────────────────────────────────

def _normalize(command: str) -> str:
    """Collapse whitespace and strip common obfuscation."""
    cmd = re.sub(r'\s+', ' ', command.strip())
    # Remove ANSI escape codes
    cmd = re.sub(r'\x1b\[[0-9;]*m', '', cmd)
    return cmd


# ─── GATE FUNCTIONS ───────────────────────────────────────────────────────────

def _check_blocked(command: str) -> tuple[bool, str]:
    """Returns (is_blocked, reason). Blocked = never run."""
    norm = _normalize(command)
    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(norm):
            return True, reason
    return False, ""


def _check_sensitive(command: str) -> tuple[bool, str]:
    """Returns (is_sensitive, reason). Sensitive = needs trust 3+."""
    norm = _normalize(command).lower()
    if any(norm.startswith(p) for p in SAFE_PREFIXES):
        return False, ""
    for pattern, reason in SENSITIVE_PATTERNS:
        if pattern.search(command):
            return True, reason
    return False, ""


def check_trust_gate(command: str, trust_level: int) -> tuple[bool, str]:
    """
    Main gate. Returns (allowed: bool, reason: str).
    Reason is empty string when allowed.
    """
    if get_config("shell_enabled", "true") == "false":
        return False, "Shell execution is disabled. Enable with /shell on"

    blocked, reason = _check_blocked(command)
    if blocked:
        _audit(command, trust_level, False, reason)
        return False, reason

    # Auto-downgrade expired God Mode. A user who set Trust 4 hours ago
    # shouldn't have it still active — they need to re-activate.
    trust_level = effective_trust(trust_level)

    if trust_level == 4:
        _audit(command, trust_level, True)
        return True, ""

    if trust_level == 3:
        sensitive, sreason = _check_sensitive(command)
        if sensitive:
            from rich.console import Console
            Console().print(f"  [yellow]WARN[/yellow] Sensitive command: {sreason}")
        _audit(command, trust_level, True, sreason if sensitive else "")
        return True, ""

    if trust_level == 2:
        sensitive, _ = _check_sensitive(command)
        norm = _normalize(command).lower()
        is_safe = any(norm.startswith(p) for p in SAFE_PREFIXES)
        if is_safe and not sensitive:
            _audit(command, trust_level, True)
            return True, ""
        try:
            answer = input(f"\033[33m  ⚠  Allow command? [{command[:60]}] (y/N): \033[0m").strip().lower()
            allowed = answer == "y"
            _audit(command, trust_level, allowed, "user prompt")
            return allowed, ("User denied." if not allowed else "")
        except (EOFError, KeyboardInterrupt):
            _audit(command, trust_level, False, "stdin unavailable")
            return False, "User cancelled."

    # Level 1: confirm every command
    try:
        answer = input(f"\033[33m  🔒  Confirm command? [{command[:60]}] (y/N): \033[0m").strip().lower()
        allowed = answer == "y"
        _audit(command, trust_level, allowed, "user prompt")
        return allowed, ("User denied." if not allowed else "")
    except (EOFError, KeyboardInterrupt):
        _audit(command, trust_level, False, "stdin unavailable")
        return False, "User cancelled."


def execute_bash(command: str, trust_level: int, on_output=None) -> str:
    """
    Execute a bash command after trust gate. Returns accumulated output string.

    on_output: optional callable(str) — called live with each output line as it
               arrives (streaming mode). Still returns full output when done.

    Timeout is configurable via the 'bash_timeout' config key (default: 60s for
    trust 1-2, 300s for trust 3-4).
    """
    from omnicli.tui import danger_blocked, info

    allowed, reason = check_trust_gate(command, trust_level)

    if not allowed:
        is_perm, perm_reason = _check_blocked(command)
        if is_perm:
            danger_blocked(command, perm_reason)
            return f"BLOCKED: {perm_reason}"
        return f"DENIED: {reason}"

    # Determine timeout: configurable, with a sensible default based on trust
    _default_timeout = 300 if trust_level >= 3 else 60
    try:
        _timeout = int(get_config("bash_timeout", str(_default_timeout)) or _default_timeout)
    except (TypeError, ValueError):
        _timeout = _default_timeout

    # Windows shell guard: reject bash-only heredoc syntax before cmd.exe
    # mangles it into "<< was unexpected at this time".
    if os.name == "nt":
        if re.search(r"<<\s*['\"]?\w+", command):
            return (
                "ERROR: Bash heredoc (`<<TAG ... TAG`) is not supported on Windows "
                "because the command runs via cmd.exe. Use one of:\n"
                "  • `python -c \"print('hi')\"` for a one-liner\n"
                "  • write_file to create a .py file, then run it with `python file.py`\n"
                "  • PowerShell here-string: `@'...'@` piped into python"
            )

    try:
        info(f"Executing: {command[:80]}")

        if on_output is not None:
            # ── Streaming mode: read stdout line by line as it arrives ───────
            proc = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            lines: list[str] = []
            try:
                for line in iter(proc.stdout.readline, ""):
                    lines.append(line)
                    on_output(line)
                    if sum(len(l) for l in lines) > 4000:
                        break
                proc.wait(timeout=_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                _audit(command, trust_level, True, f"timeout after {_timeout}s")
                return "".join(lines)[:4000] + f"\nERROR: Command timed out after {_timeout}s."
            finally:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
            output = "".join(lines)
            if proc.returncode != 0:
                output = output or f"ERROR: exit code {proc.returncode}"
            return output[:4000]

        # ── Non-streaming mode: original behaviour ────────────────────────────
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=_timeout
        )
        output = result.stdout if result.returncode == 0 else f"ERROR:\n{result.stderr}"
        return output[:4000]

    except subprocess.TimeoutExpired:
        _audit(command, trust_level, True, f"timeout after {_timeout}s")
        return f"ERROR: Command timed out after {_timeout}s."
    except Exception as e:
        return f"ERROR: {str(e)}"

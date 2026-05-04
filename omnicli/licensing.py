"""
PhantomCLI Licensing  (v2 — device-limited, encrypted cache)
─────────────────────────────────────────────────────────────
- License keys are validated online against phantom.aravindlabs.tech
- Each license supports up to 3 devices
- Device fingerprint is sent on every validation (stable per machine)
- Validated key is cached locally in ~/.omnicli/.license (Fernet-encrypted)
- Cache includes device fingerprint — copying to another machine is rejected
- Format: PHC-XXXXXXXX-XXXXXXXX-XXXXXXXX
"""

import os
import json
import re
import uuid
import hashlib
import socket
import platform as _platform
import requests

CONFIG_DIR       = os.path.expanduser("~/.omnicli")
LICENSE_FILE     = os.path.join(CONFIG_DIR, ".license")
VALIDATION_URL   = "https://phantom.aravindlabs.tech/api/phantomcli/check-license"
DEACTIVATION_URL = "https://phantom.aravindlabs.tech/api/phantomcli/deactivate-device"
DEVICES_URL      = "https://phantom.aravindlabs.tech/api/phantomcli/devices"
KEY_PATTERN      = re.compile(r'^PHC-[A-F0-9]{8}-[A-F0-9]{8}-[A-F0-9]{8}$', re.IGNORECASE)
MAX_DEVICES      = 3

os.makedirs(CONFIG_DIR, exist_ok=True)


# ─── ENCRYPTION HELPERS ───────────────────────────────────────────────────────

def _get_fernet():
    """Returns a Fernet instance using the machine key from auth module."""
    from omnicli.auth import _get_machine_key
    from cryptography.fernet import Fernet
    return Fernet(_get_machine_key())


# ─── DEVICE FINGERPRINT ───────────────────────────────────────────────────────

def get_device_id() -> str:
    """
    Returns a stable hardware-derived device fingerprint.
    Computed fresh each time — NOT cached to disk — to prevent spoofing via
    file copy. Based on MAC address + hostname + platform.
    """
    mac   = str(uuid.getnode())
    host  = socket.gethostname()
    plat  = _platform.system() + _platform.release()
    raw   = f"{mac}:{host}:{plat}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_device_name() -> str:
    """Human-readable device name: hostname (OS)."""
    try:
        host    = socket.gethostname()
        os_name = _platform.system()
        ver     = _platform.release()
        return f"{host} ({os_name} {ver})"[:64]
    except Exception:
        return "Unknown Device"


def get_platform() -> str:
    try:
        return _platform.system().lower()
    except Exception:
        return "unknown"


# ─── ENCRYPTED CACHE ──────────────────────────────────────────────────────────

def _load_cached() -> dict | None:
    """
    Load and decrypt the cached license.
    Returns None if missing, tampered, or device fingerprint doesn't match.
    """
    if not os.path.exists(LICENSE_FILE):
        return None
    try:
        with open(LICENSE_FILE, "rb") as f:
            ciphertext = f.read()
        fernet  = _get_fernet()
        plaintext = fernet.decrypt(ciphertext)
        data    = json.loads(plaintext.decode())

        # Reject cache if the stored fingerprint doesn't match this machine
        cached_fp = data.get("device_fingerprint", "")
        if cached_fp != get_device_id():
            return None

        return data
    except Exception:
        return None


def _save_cached(key: str, email: str) -> None:
    """Encrypt and cache a validated license to disk, binding it to this machine."""
    try:
        data = {
            "key":               key.upper(),
            "email":             email,
            "device_fingerprint": get_device_id(),
        }
        fernet     = _get_fernet()
        ciphertext = fernet.encrypt(json.dumps(data).encode())
        with open(LICENSE_FILE, "wb") as f:
            f.write(ciphertext)
        if os.name != "nt":
            os.chmod(LICENSE_FILE, 0o600)
    except Exception:
        pass


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def is_licensed() -> bool:
    """
    Returns True if a valid encrypted license is cached AND the device
    fingerprint matches this machine.
    Fails closed — returns False on any error.
    """
    try:
        cached = _load_cached()
        if not cached:
            return False
        key = cached.get("key", "")
        return bool(KEY_PATTERN.match(key))
    except Exception:
        return False  # Always fail closed


def get_license_info() -> dict:
    """Returns cached license info with key partially masked."""
    try:
        cached = _load_cached()
        if not cached:
            return {"licensed": False}
        return {
            "licensed": True,
            "key":   cached.get("key", "")[:12] + "••••",
            "email": cached.get("email", ""),
        }
    except Exception:
        return {"licensed": False}


def validate_key_online(key: str) -> tuple[bool, str]:
    """
    Validates a PHC key against the Phantom server.
    Registers this device (up to MAX_DEVICES per license).
    Returns (valid: bool, email_or_error: str).
    Fails CLOSED — offline with no cached license = denied.
    """
    key = key.strip().upper()
    if not KEY_PATTERN.match(key):
        return False, "Invalid key format. Expected: PHC-XXXXXXXX-XXXXXXXX-XXXXXXXX"

    try:
        resp = requests.post(
            VALIDATION_URL,
            json={
                "key":         key,
                "device_id":   get_device_id(),
                "device_name": get_device_name(),
                "platform":    get_platform(),
            },
            timeout=10,
            headers={"Content-Type": "application/json"},
        )

        if resp.status_code == 403:
            data    = resp.json()
            devices = data.get("devices", [])
            names   = [d.get("device_name", "?") for d in devices]
            msg = (
                f"Device limit reached ({MAX_DEVICES}/3).\n"
                f"Registered devices:\n"
                + "\n".join(f"  {i+1}. {n}" for i, n in enumerate(names))
                + "\n\nTo free a slot run: python run.py setup → option 7 (Manage Devices)"
            )
            return False, msg

        data = resp.json()
        if data.get("valid"):
            email = data.get("email", "")
            slots = data.get("slots_remaining")
            _save_cached(key, email)
            info  = f" ({slots} slot{'s' if slots != 1 else ''} remaining)" if slots is not None else ""
            return True, email + info
        return False, data.get("error", "Invalid or inactive license key.")

    except requests.exceptions.ConnectionError:
        # Offline grace: allow if THIS machine's encrypted cache matches
        if is_licensed():
            return True, "(offline — using cached license)"
        return False, "Could not connect to license server. Check your internet connection."
    except requests.exceptions.Timeout:
        if is_licensed():
            return True, "(offline — using cached license)"
        return False, "License server timed out. Try again."
    except Exception as e:
        return False, f"Validation error: {str(e)}"


def list_devices(key: str) -> tuple[bool, list]:
    """Fetch all registered devices for this license from the server."""
    key = key.strip().upper()
    try:
        r = requests.post(DEVICES_URL, json={"key": key}, timeout=10)
        if r.ok:
            return True, r.json().get("devices", [])
        return False, []
    except Exception:
        return False, []


def deactivate_device(key: str, device_id: str) -> tuple[bool, str]:
    """Remove a device from the license on the server."""
    key = key.strip().upper()
    try:
        r    = requests.post(
            DEACTIVATION_URL,
            json={"key": key, "device_id": device_id},
            timeout=10,
        )
        data = r.json()
        if r.ok and data.get("success"):
            return True, data.get("removed", "Device removed")
        return False, data.get("error", "Failed to deactivate")
    except Exception as e:
        return False, str(e)


def deactivate_this_device(key: str) -> tuple[bool, str]:
    """Deactivate the current machine from this license."""
    return deactivate_device(key, get_device_id())


def revoke_local_license() -> None:
    """Remove the locally cached license (doesn't deactivate on server)."""
    if os.path.exists(LICENSE_FILE):
        os.remove(LICENSE_FILE)

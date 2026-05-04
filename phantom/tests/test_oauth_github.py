"""Tests for GitHub OAuth device-code flow + GitHub Models wiring.

Network is mocked end-to-end: a fake httpx client serves canned
responses keyed off (method, URL). No live GitHub calls.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from phantom.agent.oauth_provider import (
    GitHubOAuthFlow,
    TokenSet,
)
from phantom.errors import LicenseError


# ───── fake httpx client ──────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, json_data: Any = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text or (str(json_data) if json_data is not None else "")

    def json(self) -> Any:
        return self._json


class _FakeClient:
    """Trivially scriptable httpx-shaped client.

    Build with ``responses={(method, url): [_FakeResponse, ...]}``. Each
    matched call pops the next response off the list. Unmatched calls
    raise to make missing-mock bugs loud.
    """

    def __init__(self, responses: dict[tuple[str, str], list[_FakeResponse]]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _take(self, method: str, url: str) -> _FakeResponse:
        key = (method, url)
        if key not in self.responses or not self.responses[key]:
            raise AssertionError(f"unmocked call {method} {url}")
        return self.responses[key].pop(0)

    def post(self, url: str, data: Any = None, headers: Any = None) -> _FakeResponse:
        self.calls.append(("POST", url, {"data": data, "headers": headers}))
        return self._take("POST", url)

    def get(self, url: str, headers: Any = None) -> _FakeResponse:
        self.calls.append(("GET", url, {"headers": headers}))
        return self._take("GET", url)


# ───── individual unit tests ──────────────────────────────────────────────────


def test_github_flow_requires_client_id():
    flow = GitHubOAuthFlow()  # client_id="" by default
    with pytest.raises(LicenseError) as excinfo:
        flow.begin()
    msg = excinfo.value.detail or str(excinfo.value)
    assert "github OAuth requires a client_id" in msg
    assert "PHANTOM_OAUTH_GITHUB_CLIENT_ID" in msg


def test_github_begin_sets_accept_json_header_and_parses_response():
    fake = _FakeClient({
        ("POST", "https://github.com/login/device/code"): [
            _FakeResponse(200, {
                "device_code": "DEV-XYZ",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }),
        ],
    })
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    flow.client = fake
    state = flow.begin()
    assert state["device_code"] == "DEV-XYZ"
    assert state["user_code"] == "ABCD-1234"
    # Header check — GitHub returns form-encoded by default; we MUST
    # ask for JSON or the entire flow falls over silently.
    sent_headers = fake.calls[0][2]["headers"]
    assert sent_headers.get("Accept") == "application/json"


def test_github_begin_rejects_missing_required_fields():
    fake = _FakeClient({
        ("POST", "https://github.com/login/device/code"): [
            _FakeResponse(200, {"device_code": "x"}),  # missing user_code etc.
        ],
    })
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    flow.client = fake
    with pytest.raises(LicenseError) as excinfo:
        flow.begin()
    msg = excinfo.value.detail or str(excinfo.value)
    assert "missing 'user_code'" in msg


def test_github_poll_returns_none_on_authorization_pending():
    fake = _FakeClient({
        ("POST", "https://github.com/login/oauth/access_token"): [
            _FakeResponse(200, {"error": "authorization_pending"}),
        ],
    })
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    flow.client = fake
    result = flow.poll({"device_code": "DEV-XYZ"})
    assert result is None  # caller should sleep + retry


def test_github_poll_returns_none_on_slow_down():
    fake = _FakeClient({
        ("POST", "https://github.com/login/oauth/access_token"): [
            _FakeResponse(200, {"error": "slow_down"}),
        ],
    })
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    flow.client = fake
    assert flow.poll({"device_code": "DEV-XYZ"}) is None


def test_github_poll_returns_token_on_success():
    fake = _FakeClient({
        ("POST", "https://github.com/login/oauth/access_token"): [
            _FakeResponse(200, {
                "access_token": "gho_FakeToken123",
                "token_type": "bearer",
                "scope": "",
            }),
        ],
    })
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    flow.client = fake
    tokens = flow.poll({"device_code": "DEV-XYZ"})
    assert tokens is not None
    assert tokens.access_token == "gho_FakeToken123"
    # GitHub OAuth Apps don't issue refresh tokens; expect empty.
    assert tokens.refresh_token == ""


def test_github_poll_raises_on_explicit_error():
    fake = _FakeClient({
        ("POST", "https://github.com/login/oauth/access_token"): [
            _FakeResponse(200, {"error": "expired_token"}),
        ],
    })
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    flow.client = fake
    with pytest.raises(LicenseError) as excinfo:
        flow.poll({"device_code": "DEV-XYZ"})
    assert "expired_token" in (excinfo.value.detail or str(excinfo.value))


def test_github_refresh_explicitly_unsupported():
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    with pytest.raises(LicenseError) as excinfo:
        flow.refresh("anything")
    msg = excinfo.value.detail or str(excinfo.value)
    assert "do not refresh" in msg
    assert "phantom auth login --provider github" in msg


def test_github_whoami_round_trips_user_profile():
    fake = _FakeClient({
        ("GET", "https://api.github.com/user"): [
            _FakeResponse(200, {
                "login": "aravindlabs",
                "name": "Aravind Labs",
                "email": "aravind.engineer001@gmail.com",
                "plan": {"name": "free"},
                "public_repos": 42,
            }),
        ],
    })
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    flow.client = fake
    info = flow.whoami("gho_FakeToken123")
    assert info["login"] == "aravindlabs"
    assert info["public_repos"] == 42
    # Sent the right auth header
    sent_headers = fake.calls[0][2]["headers"]
    assert sent_headers["Authorization"] == "Bearer gho_FakeToken123"
    assert "PhantomCLI/" in sent_headers["User-Agent"]


def test_github_whoami_rejects_blank_token():
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    with pytest.raises(LicenseError) as excinfo:
        flow.whoami("")
    assert "requires an access_token" in (excinfo.value.detail or str(excinfo.value))


def test_github_whoami_surfaces_403_with_body():
    fake = _FakeClient({
        ("GET", "https://api.github.com/user"): [
            _FakeResponse(403, text='{"message":"Bad credentials"}'),
        ],
    })
    flow = GitHubOAuthFlow(client_id="Iv23.test")
    flow.client = fake
    with pytest.raises(LicenseError) as excinfo:
        flow.whoami("gho_StaleToken")
    msg = excinfo.value.detail or str(excinfo.value)
    assert "403" in msg
    assert "Bad credentials" in msg


def test_github_models_default_endpoint_and_model():
    """Sanity-check the constants we'll wire into the engine config."""
    flow = GitHubOAuthFlow(client_id="anything")
    assert flow.models_base_url == "https://models.github.ai/inference"
    assert flow.default_model == "gpt-4o"


def test_github_in_phantom_cli_auth_flows_dict():
    """Ensure the CLI dispatcher actually knows about the new provider."""
    from phantom.cli.auth import _FLOWS, build_flow
    assert "github" in _FLOWS
    flow = build_flow("github")
    assert isinstance(flow, GitHubOAuthFlow)


def test_phantom_cli_top_level_login_shortcut_exists():
    """`phantom login` (no `auth`) must be registered for ergonomics."""
    import phantom.cli
    names = {c.name for c in phantom.cli.app.registered_commands}
    assert "login" in names
    assert "logout" in names
    assert "whoami" in names


# ───── machine-key derivation must work cross-platform ────────────────────────


def test_machine_key_works_when_os_getuid_is_missing(monkeypatch, tmp_path):
    """Windows lacks ``os.getuid``. The fallback must still produce a key.

    Regression: shipped 4.0.1/4.0.2 where the Windows fallback path called
    ``os.getuid()`` and crashed with AttributeError on first OAuth save,
    after the user had already authorised the device code.
    """
    import os as _os
    import phantom.agent.oauth_provider as op

    # Force the /etc/machine-id loop to come up empty so we hit the fallback.
    monkeypatch.setattr(op, "Path", lambda *a, **kw: _UnreadablePath())
    # Hide os.getuid so the code path matches Windows.
    if hasattr(_os, "getuid"):
        monkeypatch.delattr(_os, "getuid")
    # Ensure the env-fallback path has *something* sensible to work with.
    monkeypatch.setenv("USERNAME", "aravi")

    key = op._machine_key()
    # Fernet keys are URL-safe base64 of 32 bytes -> 44 chars including padding.
    assert isinstance(key, bytes)
    assert len(key) == 44
    # And the key must be deterministic for the same hostname+username pair.
    assert op._machine_key() == key


class _UnreadablePath:
    """Stand-in for pathlib.Path that always raises OSError on read_bytes."""
    def read_bytes(self):
        raise OSError("simulated missing /etc/machine-id")


# ───── Google Gemini OAuth (also OpenAI-compat-shaped Bearer) ─────────────────


def test_google_flow_requires_client_id():
    from phantom.agent.oauth_provider import GoogleOAuthFlow
    flow = GoogleOAuthFlow()
    with pytest.raises(LicenseError) as excinfo:
        flow.begin()
    msg = excinfo.value.detail or str(excinfo.value)
    assert "google OAuth requires a client_id" in msg
    assert "TVs and Limited Input devices" in msg


def test_google_begin_normalises_legacy_verification_url_field():
    """Google sometimes returns ``verification_url`` (legacy) instead of
    the RFC 8628 ``verification_uri``. The flow must normalise to the
    new field name so callers don't break."""
    from phantom.agent.oauth_provider import GoogleOAuthFlow
    fake = _FakeClient({
        ("POST", "https://oauth2.googleapis.com/device/code"): [
            _FakeResponse(200, {
                "device_code": "GAID-XYZ",
                "user_code": "ABCD-EFGH",
                "verification_url": "https://www.google.com/device",  # legacy
                "expires_in": 1800,
                "interval": 5,
            }),
        ],
    })
    flow = GoogleOAuthFlow(client_id="cid.apps.googleusercontent.com")
    flow.client = fake
    state = flow.begin()
    # Both should be present for callers that read either field.
    assert state["verification_uri"] == "https://www.google.com/device"
    assert state["verification_url"] == "https://www.google.com/device"


def test_google_begin_accepts_modern_verification_uri_field():
    from phantom.agent.oauth_provider import GoogleOAuthFlow
    fake = _FakeClient({
        ("POST", "https://oauth2.googleapis.com/device/code"): [
            _FakeResponse(200, {
                "device_code": "GAID-XYZ",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://www.google.com/device",
                "expires_in": 1800,
                "interval": 5,
            }),
        ],
    })
    flow = GoogleOAuthFlow(client_id="cid.apps.googleusercontent.com")
    flow.client = fake
    state = flow.begin()
    assert state["verification_uri"] == "https://www.google.com/device"


def test_google_models_default_endpoint_and_model():
    """Engine config must wire to the OpenAI-compat shim, not the native Gemini API."""
    from phantom.agent.oauth_provider import GoogleOAuthFlow
    flow = GoogleOAuthFlow(client_id="anything")
    assert flow.models_base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert flow.default_model == "gemini-2.0-flash"


def test_google_whoami_round_trips_userinfo():
    from phantom.agent.oauth_provider import GoogleOAuthFlow
    fake = _FakeClient({
        ("GET", "https://www.googleapis.com/oauth2/v2/userinfo"): [
            _FakeResponse(200, {
                "email": "aravind.engineer001@gmail.com",
                "name": "Aravind",
                "verified_email": True,
                "locale": "en",
            }),
        ],
    })
    flow = GoogleOAuthFlow(client_id="cid.apps.googleusercontent.com")
    flow.client = fake
    info = flow.whoami("ya29.fake-token")
    assert info["email"] == "aravind.engineer001@gmail.com"
    sent_headers = fake.calls[0][2]["headers"]
    assert sent_headers["Authorization"] == "Bearer ya29.fake-token"


def test_google_whoami_rejects_blank_token():
    from phantom.agent.oauth_provider import GoogleOAuthFlow
    flow = GoogleOAuthFlow(client_id="cid.apps.googleusercontent.com")
    with pytest.raises(LicenseError):
        flow.whoami("")


def test_google_whoami_surfaces_401_with_body():
    from phantom.agent.oauth_provider import GoogleOAuthFlow
    fake = _FakeClient({
        ("GET", "https://www.googleapis.com/oauth2/v2/userinfo"): [
            _FakeResponse(401, text='{"error":{"message":"Invalid Credentials"}}'),
        ],
    })
    flow = GoogleOAuthFlow(client_id="cid.apps.googleusercontent.com")
    flow.client = fake
    with pytest.raises(LicenseError) as excinfo:
        flow.whoami("ya29.stale")
    msg = excinfo.value.detail or str(excinfo.value)
    assert "401" in msg
    assert "Invalid Credentials" in msg


def test_phantom_cli_whoami_supports_google():
    """The CLI dispatcher must accept --provider google after this fix."""
    from phantom.cli.auth import _FLOWS
    assert "google" in _FLOWS


# ───── ~/.phantom/.env persistence (4.0.6 regression) ────────────────────────


def test_load_phantom_env_reads_keys_from_dotenv(monkeypatch, tmp_path):
    """OAuth client_ids written to ~/.phantom/.env must survive restarts.

    Regression: shipped 4.0.4/4.0.5 wrote ~/.phantom/.env in the setup
    walkthroughs but never loaded it at startup, so PHANTOM_OAUTH_*
    env vars vanished after each `phantom update`.
    """
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    env_dir = home / ".phantom"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "PHANTOM_OAUTH_GITHUB_CLIENT_ID=Iv23.persisted\n"
        "# a comment line\n"
        "BLANK_LINE_TEST=\n"
        "\n"
        'QUOTED_VAL="quoted value"\n',
        encoding="utf-8",
    )
    # Make sure these aren't already set in the process before we test.
    monkeypatch.delenv("PHANTOM_OAUTH_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("BLANK_LINE_TEST", raising=False)
    monkeypatch.delenv("QUOTED_VAL", raising=False)

    import omnicli.cli as cli
    n = cli._load_phantom_env()
    assert n >= 2  # at least the two non-blank, non-comment keys with values
    import os
    assert os.environ["PHANTOM_OAUTH_GITHUB_CLIENT_ID"] == "Iv23.persisted"
    assert os.environ["QUOTED_VAL"] == "quoted value"  # quotes stripped


def test_load_phantom_env_does_not_overwrite_shell_export(monkeypatch, tmp_path):
    """Shell-export precedence: an env var already set in os.environ wins
    over a value in the dotfile. Standard dotenv semantics."""
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / ".phantom").mkdir()
    (home / ".phantom" / ".env").write_text(
        "PRECEDENCE_TEST=from_dotfile\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRECEDENCE_TEST", "from_shell")

    import omnicli.cli as cli
    cli._load_phantom_env()
    import os
    assert os.environ["PRECEDENCE_TEST"] == "from_shell"


def test_load_phantom_env_no_file_returns_zero(monkeypatch, tmp_path):
    """No ~/.phantom/.env present → loader is a no-op, returns 0."""
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    import omnicli.cli as cli
    assert cli._load_phantom_env() == 0


# ───── Model picker catalogs ─────────────────────────────────────────────────


def test_github_model_catalog_has_expected_options():
    """Smoke-test the GitHub Models picker catalog hasn't lost its shape."""
    from omnicli.cli import _GITHUB_MODELS
    names = [m[0] for m in _GITHUB_MODELS]
    assert "gpt-4o" in names
    assert "gpt-4o-mini" in names
    assert "Llama-3.3-70B-Instruct" in names
    assert "Phi-4" in names


def test_gemini_model_catalog_has_expected_options():
    """Smoke-test the Gemini picker catalog."""
    from omnicli.cli import _GEMINI_MODELS
    names = [m[0] for m in _GEMINI_MODELS]
    assert "gemini-2.0-flash" in names
    assert "gemini-1.5-pro" in names


# ───── update verification (4.0.7 regression) ────────────────────────────────


def test_read_on_disk_omnicli_version_parses_simple_init(monkeypatch, tmp_path):
    """Update success-path must read the freshly-extracted version straight
    from disk, not the cached in-memory ``__version__`` of the running
    process. Without this, users couldn't tell when an update silently
    no-op'd because of stale __pycache__ shadowing.
    """
    fake_install = tmp_path / "install"
    (fake_install / "omnicli").mkdir(parents=True)
    (fake_install / "omnicli" / "__init__.py").write_text(
        '"""docstring"""\n__version__ = "9.9.9"\n'
    )
    import omnicli.cli as cli
    # Point INSTALL_DIR at our fake tree just for this read.
    monkeypatch.setattr(cli, "INSTALL_DIR", fake_install)
    assert cli._read_on_disk_omnicli_version() == "9.9.9"


def test_read_on_disk_omnicli_version_returns_empty_when_missing(monkeypatch, tmp_path):
    import omnicli.cli as cli
    monkeypatch.setattr(cli, "INSTALL_DIR", tmp_path)  # no omnicli/__init__.py here
    assert cli._read_on_disk_omnicli_version() == ""

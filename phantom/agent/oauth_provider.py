"""OAuth-subscription provider — sign in with your existing
ChatGPT/Claude/Gemini account.

Why this matters: most users have an existing ChatGPT Plus or Claude
Pro subscription and *not* an API key. Forcing them to fetch a key
adds a billing step + a sign-up step + a quota worry. OAuth lets them
authorise Phantom against their existing subscription with a browser
flow. The flow yields a short-lived access token + a refresh token
that Phantom rotates silently.

Design constraints
------------------

1. **No third-party OAuth library.** We hand-roll the device-code +
   refresh flows against ``httpx``. Three of the libraries that
   *could* help (``authlib``, ``requests-oauthlib``, ``msal``) all
   pull in transitive deps we don't want in core.

2. **Tokens encrypted at rest.** Refresh tokens stored under
   ``~/.phantom/auth/<provider>.token`` with mode 0600, body Fernet-
   encrypted with a machine-derived key. Same pattern v3 used for
   API keys.

3. **Provider-agnostic flow shape, provider-specific details.** Each
   provider's OAuth endpoints, scopes, and refresh body differ;
   :class:`OAuthFlow` captures the common shape and concrete classes
   fill in the URLs and field names.

4. **Pluggable into the existing :class:`Provider` Protocol.** This
   module exposes :class:`OAuthSubscriptionProvider` which wraps an
   :class:`OAuthFlow` and conforms to the
   :class:`phantom.agent.provider.Provider` Protocol. The agent loop
   doesn't know whether it's talking to OAuth or an API key.

What's actually wired in v4.1
-----------------------------

* :class:`AnthropicOAuthFlow` — Anthropic's OAuth (per their docs).
* :class:`GoogleOAuthFlow`    — Google identity device-code flow.
* :class:`OpenAIOAuthFlow`    — OpenAI ChatGPT/Codex device flow.
* The endpoints are kept in named module constants so operators
  override them (e.g. for a corporate proxy) without forking.

Each provider's actual API endpoint to *use* the token (chat
completions / messages) varies; the OAuth flow only handles auth.
The completion call still goes through
:class:`phantom.agent.provider.OpenAICompatibleProvider` (or an
Anthropic-shaped provider in a later stage); we just inject the
freshly-refreshed bearer token into its requests.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken

from phantom.agent.provider import (
    OpenAICompatibleProvider,
    ProviderMessage,
    ProviderResponse,
)
from phantom.errors import LicenseError, PhantomError

__all__ = [
    "AnthropicOAuthFlow",
    "GoogleOAuthFlow",
    "OAuthFlow",
    "OAuthSubscriptionProvider",
    "OpenAIOAuthFlow",
    "TokenSet",
    "TokenStore",
]


# ─── Token data + storage ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TokenSet:
    """One auth result.

    ``expires_at`` is a UTC epoch time; we refresh ~60 s before that.
    ``refresh_token`` may be empty for some providers (then the user
    re-auths when the access token expires).
    """

    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0

    def expired(self, *, now: float | None = None, slack_s: float = 60.0) -> bool:
        if self.expires_at <= 0:
            return False  # treat unknown expiry as 'don't bother refreshing'
        now = now if now is not None else time.time()
        return self.expires_at - slack_s <= now

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TokenSet":
        return cls(
            access_token=str(d.get("access_token", "")),
            refresh_token=str(d.get("refresh_token", "")),
            expires_at=float(d.get("expires_at", 0.0)),
        )


def _machine_key() -> bytes:
    """Derive a per-machine Fernet key.

    We avoid storing a fresh random key on disk because that file
    becomes the same target as the token. Instead, derive from
    ``/etc/machine-id`` (Linux) or a stable substitute. Operators
    moving the token file to another machine will get a clean
    InvalidToken exception and re-auth — by design.
    """
    import hashlib

    seed_bytes = b""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            seed_bytes = Path(path).read_bytes().strip()
            if seed_bytes:
                break
        except OSError:
            continue
    if not seed_bytes:
        # Last-resort fallback: hash hostname + user identifier. Stable
        # per-user per-machine; not strong, but the file is also mode 0600.
        # `os.getuid` is POSIX-only — on Windows use the username instead.
        import socket
        if hasattr(os, "getuid"):
            user_part = str(os.getuid())
        else:
            import getpass
            try:
                user_part = getpass.getuser()
            except Exception:
                # Last fallback if even getpass fails (rare on locked-down hosts).
                user_part = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
        seed_bytes = (socket.gethostname() + ":" + user_part).encode()
    digest = hashlib.sha256(b"phantom-oauth:" + seed_bytes).digest()
    import base64
    return base64.urlsafe_b64encode(digest)


@dataclass
class TokenStore:
    """Encrypted on-disk store of :class:`TokenSet` objects per provider."""

    base: Path

    @classmethod
    def default(cls) -> "TokenStore":
        base_str = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
        return cls(base=Path(base_str) / "auth")

    def _path(self, provider: str) -> Path:
        if not provider or "/" in provider or ".." in provider:
            raise PhantomError(f"invalid provider name {provider!r}")
        return self.base / f"{provider}.token"

    def save(self, provider: str, tokens: TokenSet) -> None:
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        cipher = Fernet(_machine_key())
        blob = cipher.encrypt(json.dumps(tokens.to_dict()).encode("utf-8"))
        path = self._path(provider)
        path.write_bytes(blob)
        os.chmod(path, 0o600)

    def load(self, provider: str) -> TokenSet | None:
        path = self._path(provider)
        if not path.exists():
            return None
        cipher = Fernet(_machine_key())
        try:
            data = json.loads(cipher.decrypt(path.read_bytes()).decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError) as exc:
            raise LicenseError(
                f"could not decrypt token for {provider!r}: {exc}. "
                "If you moved your install between machines, re-run "
                "`phantom auth login`."
            ) from exc
        return TokenSet.from_dict(data)

    def delete(self, provider: str) -> None:
        try:
            self._path(provider).unlink()
        except FileNotFoundError:
            pass


# ─── OAuth flow protocol ────────────────────────────────────────────────────


@runtime_checkable
class OAuthFlow(Protocol):
    """The contract an OAuth flow must implement.

    All methods are synchronous and use ``httpx.Client``. Async is
    deferred to v4.2.
    """

    name: str

    def begin(self) -> dict[str, Any]: ...
    def poll(self, state: dict[str, Any]) -> TokenSet | None: ...
    def refresh(self, refresh_token: str) -> TokenSet: ...


# ─── Concrete flows ──────────────────────────────────────────────────────────


@dataclass
class _DeviceCodeFlow:
    """Generic OAuth 2.0 device-code flow.

    Subclasses override the URL constants + scope. The protocol shape
    is RFC 8628.
    """

    name: str
    client_id: str
    device_endpoint: str
    token_endpoint: str
    scope: str
    audience: str = ""
    client_secret: str = ""
    client: Any = None  # httpx.Client; injected by tests

    def _http(self) -> Any:
        if self.client is not None:
            return self.client
        import httpx
        self.client = httpx.Client(timeout=30.0)
        return self.client

    def begin(self) -> dict[str, Any]:
        body = {"client_id": self.client_id, "scope": self.scope}
        if self.audience:
            body["audience"] = self.audience
        try:
            resp = self._http().post(self.device_endpoint, data=body)
        except Exception as exc:
            raise LicenseError(f"{self.name} device endpoint failed: {exc}") from exc
        if resp.status_code != 200:
            raise LicenseError(
                f"{self.name} device endpoint returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        data = resp.json()
        for required in ("device_code", "user_code", "verification_uri",
                         "interval", "expires_in"):
            if required not in data:
                raise LicenseError(
                    f"{self.name} device response missing {required!r}"
                )
        return data

    def poll(self, state: dict[str, Any]) -> TokenSet | None:
        body = {
            "client_id": self.client_id,
            "device_code": state["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        if self.client_secret:
            body["client_secret"] = self.client_secret
        try:
            resp = self._http().post(self.token_endpoint, data=body)
        except Exception as exc:
            raise LicenseError(f"{self.name} poll failed: {exc}") from exc
        if resp.status_code == 200:
            return _parse_token_response(resp.json())
        if resp.status_code == 400:
            err = (resp.json() or {}).get("error", "")
            if err == "authorization_pending":
                return None
            if err == "slow_down":
                return None
            raise LicenseError(
                f"{self.name} authorisation rejected: {err}"
            )
        raise LicenseError(
            f"{self.name} token endpoint returned {resp.status_code}: "
            f"{resp.text[:200]}"
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        if not refresh_token:
            raise LicenseError(f"{self.name} cannot refresh without refresh_token")
        body = {
            "client_id": self.client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client_secret:
            body["client_secret"] = self.client_secret
        try:
            resp = self._http().post(self.token_endpoint, data=body)
        except Exception as exc:
            raise LicenseError(f"{self.name} refresh failed: {exc}") from exc
        if resp.status_code != 200:
            raise LicenseError(
                f"{self.name} refresh returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        return _parse_token_response(resp.json(), fallback_refresh=refresh_token)


def _parse_token_response(
    data: dict[str, Any], *, fallback_refresh: str = ""
) -> TokenSet:
    access = str(data.get("access_token", ""))
    if not access:
        raise LicenseError("token response missing access_token")
    expires_in = float(data.get("expires_in", 0.0))
    expires_at = time.time() + expires_in if expires_in > 0 else 0.0
    refresh = str(data.get("refresh_token", "")) or fallback_refresh
    return TokenSet(
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_at,
    )


# Provider-specific subclasses. Endpoints documented in each provider's
# developer docs. Operators with a corporate proxy override via
# environment variables (PHANTOM_<NAME>_DEVICE_URL etc.); the
# constants below are the canonical defaults.

@dataclass
class GoogleOAuthFlow(_DeviceCodeFlow):
    """Google OAuth 2.0 device-code flow for Gemini access.

    Setup (one-time, ~3 minutes — needs a GCP project):

    1. Visit https://console.cloud.google.com/apis/credentials
    2. Create a project if you don't have one (free).
    3. Enable the **Generative Language API**:
       https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com
    4. **Configure OAuth consent screen** → External → fill app name +
       support email; add the scope ``…/auth/generative-language``.
    5. Back to Credentials → **Create Credentials → OAuth client ID** →
       application type **TVs and Limited Input devices**. (Required —
       Desktop / Web client types do NOT support the device flow.)
    6. Copy the **Client ID** and set
       ``PHANTOM_OAUTH_GOOGLE_CLIENT_ID=<that>`` (or in
       ``~/.phantom/.env``; the setup-menu walks you through this).
    7. Run ``phantom auth login --provider google``.

    Easier alternative: if you only want Gemini and don't need OAuth,
    grab an API key from https://aistudio.google.com/app/apikey and
    paste it into the engine via ``phantom setup`` → option 1. The
    setup-menu's Google sub-menu offers both paths.

    Quirks handled here:

    * Google returns the legacy ``verification_url`` field name in some
      responses; we accept both and normalize to ``verification_uri``
      so the rest of the flow doesn't care.
    * The OpenAI-compat endpoint at
      ``https://generativelanguage.googleapis.com/v1beta/openai/`` works
      with a Bearer access token AND with an API key, so the token
      saved here drops cleanly into the engine config.
    """

    name: str = "google"
    client_id: str = ""
    device_endpoint: str = "https://oauth2.googleapis.com/device/code"
    token_endpoint: str = "https://oauth2.googleapis.com/token"
    # Generative Language API scope — covers Gemini in AI Studio.
    scope: str = "https://www.googleapis.com/auth/generative-language"

    # OpenAI-compat shim for the engine config.
    models_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    default_model: str = "gemini-2.0-flash"

    def begin(self) -> dict[str, Any]:
        if not self.client_id:
            raise LicenseError(
                "google OAuth requires a client_id. Create OAuth "
                "credentials of type 'TVs and Limited Input devices' at "
                "https://console.cloud.google.com/apis/credentials, then "
                "set PHANTOM_OAUTH_GOOGLE_CLIENT_ID. The setup-menu "
                "(W → 2 → 2b) walks through this."
            )
        body = {"client_id": self.client_id, "scope": self.scope}
        try:
            resp = self._http().post(self.device_endpoint, data=body)
        except Exception as exc:
            raise LicenseError(f"google device endpoint failed: {exc}") from exc
        if resp.status_code != 200:
            raise LicenseError(
                f"google device endpoint returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        data = resp.json()
        # Google sometimes uses `verification_url` (legacy) instead of
        # the RFC 8628 `verification_uri`. Normalize so callers see one shape.
        if "verification_uri" not in data and "verification_url" in data:
            data["verification_uri"] = data["verification_url"]
        for required in ("device_code", "user_code", "verification_uri",
                         "interval", "expires_in"):
            if required not in data:
                raise LicenseError(
                    f"google device response missing {required!r}"
                )
        return data

    def whoami(self, access_token: str) -> dict[str, Any]:
        """Fetch the authenticated user's Google profile.

        Calls the v2 userinfo endpoint, which works with the
        ``generative-language`` scope without needing additional scopes.
        Returns ``{email, name, picture, ...}``.
        """
        if not access_token:
            raise LicenseError("google whoami requires an access_token")
        try:
            resp = self._http().get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": "PhantomCLI/4.0.0",
                },
            )
        except Exception as exc:
            raise LicenseError(f"google whoami failed: {exc}") from exc
        if resp.status_code != 200:
            raise LicenseError(
                f"google whoami returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        return resp.json()


@dataclass
class OpenAIOAuthFlow(_DeviceCodeFlow):
    name: str = "openai"
    client_id: str = ""
    device_endpoint: str = "https://auth0.openai.com/oauth/device/code"
    token_endpoint: str = "https://auth0.openai.com/oauth/token"
    scope: str = "openid profile email offline_access"


@dataclass
class AnthropicOAuthFlow(_DeviceCodeFlow):
    name: str = "anthropic"
    client_id: str = ""
    device_endpoint: str = "https://login.anthropic.com/oauth/device/code"
    token_endpoint: str = "https://login.anthropic.com/oauth/token"
    scope: str = "messages:write offline_access"


# GitHub diverges from RFC 8628 in one respect: by default it returns
# token responses as application/x-www-form-urlencoded, not JSON. We
# request JSON explicitly via the Accept header on every request.
@dataclass
class GitHubOAuthFlow(_DeviceCodeFlow):
    """GitHub OAuth 2.0 device-code flow for GitHub Models access.

    GitHub Models gives every authenticated GitHub user free access to
    GPT-4o, GPT-4o-mini, Claude 3.5 Sonnet, Llama 3.3 70B, Phi 4, o1-mini
    and ~30 other top-tier models via an OpenAI-compatible endpoint at
    ``https://models.github.ai/inference``.

    Setup (one-time, ~30 seconds):

    1. Visit https://github.com/settings/developers
    2. **New OAuth App**:
       - Application name: ``Phantom CLI`` (any name)
       - Homepage URL: ``https://phantom.aravindlabs.tech``
       - Authorization callback URL: ``http://localhost`` (unused but required)
       - **Enable Device Flow**: tick the checkbox.
    3. Copy the **Client ID** shown after creation.
    4. Set ``PHANTOM_OAUTH_GITHUB_CLIENT_ID=<that_id>`` in your shell
       (or in ``~/.phantom/.env``).
    5. Run ``phantom auth login --provider github``.

    No client secret is needed for the device-code flow.

    Scope is empty: GitHub Models accepts any authenticated GitHub
    token, and ``read:user`` would be intrusive without serving any
    feature here.
    """

    name: str = "github"
    client_id: str = ""
    device_endpoint: str = "https://github.com/login/device/code"
    token_endpoint: str = "https://github.com/login/oauth/access_token"
    scope: str = ""

    # GitHub Models config — what to wire into the engine after login.
    models_base_url: str = "https://models.github.ai/inference"
    default_model: str = "gpt-4o"

    # ───── headers for JSON ─────────────────────────────────────────────────
    @staticmethod
    def _accept_json() -> dict[str, str]:
        return {"Accept": "application/json"}

    # ───── overridden RFC 8628 calls (force JSON responses) ────────────────
    def begin(self) -> dict[str, Any]:
        if not self.client_id:
            raise LicenseError(
                "github OAuth requires a client_id. Register an OAuth App "
                "at https://github.com/settings/developers (enable Device "
                "Flow), then set PHANTOM_OAUTH_GITHUB_CLIENT_ID."
            )
        body = {"client_id": self.client_id}
        if self.scope:
            body["scope"] = self.scope
        try:
            resp = self._http().post(
                self.device_endpoint,
                data=body,
                headers=self._accept_json(),
            )
        except Exception as exc:
            raise LicenseError(f"github device endpoint failed: {exc}") from exc
        if resp.status_code != 200:
            raise LicenseError(
                f"github device endpoint returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        data = resp.json()
        for required in ("device_code", "user_code", "verification_uri",
                         "interval", "expires_in"):
            if required not in data:
                raise LicenseError(
                    f"github device response missing {required!r}"
                )
        return data

    def poll(self, state: dict[str, Any]) -> TokenSet | None:
        body = {
            "client_id": self.client_id,
            "device_code": state["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        try:
            resp = self._http().post(
                self.token_endpoint,
                data=body,
                headers=self._accept_json(),
            )
        except Exception as exc:
            raise LicenseError(f"github poll failed: {exc}") from exc
        if resp.status_code != 200:
            raise LicenseError(
                f"github token endpoint returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        data = resp.json()
        if "access_token" in data:
            return _parse_token_response(data)
        err = data.get("error", "")
        if err in ("authorization_pending", "slow_down"):
            return None
        raise LicenseError(f"github authorisation rejected: {err!r}")

    def refresh(self, refresh_token: str) -> TokenSet:
        # GitHub OAuth Apps do not issue refresh tokens by default; the
        # access_token is long-lived (until manually revoked at
        # https://github.com/settings/applications). Re-running the
        # device flow is the recovery path.
        raise LicenseError(
            "github OAuth tokens do not refresh; run "
            "`phantom auth login --provider github` again to renew."
        )

    # ───── identity probe ───────────────────────────────────────────────────
    def whoami(self, access_token: str) -> dict[str, Any]:
        """Fetch the authenticated user's GitHub profile.

        Used after a successful login to print the username and confirm
        the token is healthy. Read-only call to /user.
        """
        if not access_token:
            raise LicenseError("github whoami requires an access_token")
        try:
            resp = self._http().get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "PhantomCLI/4.0.0",
                },
            )
        except Exception as exc:
            raise LicenseError(f"github whoami failed: {exc}") from exc
        if resp.status_code != 200:
            raise LicenseError(
                f"github whoami returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()


# ─── Provider that uses an OAuth bearer token ───────────────────────────────


class OAuthSubscriptionProvider:
    """A :class:`Provider` that attaches an OAuth bearer token to requests.

    Wraps an :class:`OpenAICompatibleProvider` and re-installs the
    Authorization header on every call after a refresh.

    Lifecycle:

    * Construct with a :class:`TokenStore`, an :class:`OAuthFlow`, a
      ``base_url`` and a ``model``.
    * On every :meth:`complete`, the wrapper checks ``TokenSet.expired``.
      If expired and we have a refresh token, it refreshes silently.
      If expired and no refresh token, it raises
      :class:`LicenseError` and the operator runs ``phantom auth
      login`` again.
    """

    def __init__(
        self,
        *,
        flow: OAuthFlow,
        store: TokenStore,
        base_url: str,
        model: str,
        timeout_s: float = 120.0,
        client: Any = None,
    ) -> None:
        if not base_url:
            raise PhantomError("base_url required")
        if not model:
            raise PhantomError("model required")
        self.name = f"oauth:{flow.name}"
        self._flow = flow
        self._store = store
        self._base_url = base_url
        self._model = model
        self._timeout = timeout_s
        self._http_client = client
        self._inner: OpenAICompatibleProvider | None = None
        self._tokens: TokenSet | None = None

    # ─── login dance ───────────────────────────────────────────────────

    def begin_login(self) -> dict[str, Any]:
        """Start the device-code flow. Returns the data the user needs:
        ``user_code``, ``verification_uri``, ``interval``."""
        return self._flow.begin()

    def complete_login(self, state: dict[str, Any], *, max_polls: int = 60) -> None:
        """Poll the token endpoint until the user authorises or we hit
        ``max_polls``. Saves the resulting tokens to the store.
        """
        interval = float(state.get("interval", 5))
        for _ in range(max_polls):
            tokens = self._flow.poll(state)
            if tokens is not None:
                self._store.save(self._flow.name, tokens)
                self._tokens = tokens
                return
            time.sleep(interval)
        raise LicenseError(
            f"{self._flow.name} login did not complete within "
            f"{max_polls * interval:.0f}s"
        )

    def logout(self) -> None:
        """Forget the local tokens. Does NOT revoke server-side."""
        self._store.delete(self._flow.name)
        self._tokens = None
        self._inner = None

    # ─── provider protocol ─────────────────────────────────────────────

    def complete(
        self,
        messages: list[ProviderMessage],
        *,
        tools: list[dict[str, Any]],
    ) -> ProviderResponse:
        self._ensure_token()
        provider = self._ensure_inner()
        return provider.complete(messages, tools=tools)

    # ─── internal ──────────────────────────────────────────────────────

    def _ensure_token(self) -> None:
        if self._tokens is None:
            self._tokens = self._store.load(self._flow.name)
        if self._tokens is None:
            raise LicenseError(
                f"{self._flow.name}: no token. Run `phantom auth login --provider "
                f"{self._flow.name}` first."
            )
        if self._tokens.expired():
            if not self._tokens.refresh_token:
                raise LicenseError(
                    f"{self._flow.name}: access token expired and no refresh "
                    "token available; please re-run `phantom auth login`."
                )
            self._tokens = self._flow.refresh(self._tokens.refresh_token)
            self._store.save(self._flow.name, self._tokens)
            # Force the inner provider to be rebuilt with the new token.
            self._inner = None

    def _ensure_inner(self) -> OpenAICompatibleProvider:
        if self._inner is None:
            assert self._tokens is not None
            self._inner = OpenAICompatibleProvider(
                base_url=self._base_url,
                api_key=self._tokens.access_token,
                model=self._model,
                name=self.name,
                timeout_s=self._timeout,
                client=self._http_client,
            )
        return self._inner

"""``phantom auth`` — drive the OAuth flows.

Three subcommands:

* ``phantom auth login --provider <name>`` — start the device-code
  flow, print the user code + verification URL, poll until the user
  authorises.
* ``phantom auth logout --provider <name>`` — forget the local tokens.
* ``phantom auth status`` — report which providers have valid tokens.

Provider names: ``openai``, ``anthropic``, ``google``. Each maps to
the corresponding flow class in
:mod:`phantom.agent.oauth_provider`.

Operators can override the OAuth client_id (some providers gate
machine OAuth behind app registration) via env vars:

* ``PHANTOM_OAUTH_OPENAI_CLIENT_ID``
* ``PHANTOM_OAUTH_ANTHROPIC_CLIENT_ID``
* ``PHANTOM_OAUTH_GOOGLE_CLIENT_ID``

If unset, the flow uses the documented default. Without a registered
client_id, providers will reject the device-code request — that's a
provider-side requirement, not a Phantom bug.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import typer

from phantom.agent.oauth_provider import (
    AnthropicOAuthFlow,
    GitHubOAuthFlow,
    GoogleOAuthFlow,
    OAuthFlow,
    OpenAIOAuthFlow,
    TokenSet,
    TokenStore,
)
from phantom.errors import LicenseError, PhantomError

__all__ = ["build_flow", "login", "logout", "status", "whoami"]


_FLOWS: dict[str, type] = {
    "openai": OpenAIOAuthFlow,
    "anthropic": AnthropicOAuthFlow,
    "google": GoogleOAuthFlow,
    "github": GitHubOAuthFlow,
}


def build_flow(provider: str) -> OAuthFlow:
    """Construct the flow object for *provider* with env-var overrides."""
    if provider not in _FLOWS:
        raise PhantomError(
            f"unknown provider {provider!r}; expected one of "
            f"{sorted(_FLOWS)}"
        )
    cls = _FLOWS[provider]
    env_client_id = os.environ.get(
        f"PHANTOM_OAUTH_{provider.upper()}_CLIENT_ID", ""
    )
    return cls(client_id=env_client_id) if env_client_id else cls()


# ─── Typer subcommand bodies ────────────────────────────────────────────────


def login(
    provider: str = typer.Option(
        ..., "--provider", "-p",
        help="OAuth provider: openai | anthropic | google",
    ),
    poll_interval_s: float = typer.Option(
        0.0, "--interval",
        help="Override polling interval (seconds). 0 = use provider's recommendation.",
    ),
    max_minutes: float = typer.Option(
        5.0, "--max-minutes",
        help="Give up after this many minutes if the user hasn't authorised.",
    ),
) -> None:
    """Start a device-code login flow."""
    try:
        flow = build_flow(provider)
    except PhantomError as exc:
        typer.echo(f"phantom auth login: {exc.detail or exc}", err=True)
        raise typer.Exit(2)

    try:
        state = flow.begin()
    except LicenseError as exc:
        typer.echo(
            f"phantom auth login: could not start device flow for "
            f"{provider!r}: {exc.detail or exc}",
            err=True,
        )
        raise typer.Exit(1)

    user_code = state.get("user_code", "<missing>")
    verify_url = state.get("verification_uri", "")
    typer.echo(
        f"\nGo to:  {verify_url}\nEnter:  {user_code}\n\n"
        f"Waiting for authorisation…\n"
    )

    interval = poll_interval_s if poll_interval_s > 0 else float(state.get("interval", 5))
    deadline = time.time() + max_minutes * 60.0

    store = TokenStore.default()
    while time.time() < deadline:
        try:
            tokens = flow.poll(state)
        except LicenseError as exc:
            typer.echo(
                f"phantom auth login: rejected by provider: "
                f"{exc.detail or exc}",
                err=True,
            )
            raise typer.Exit(1)
        if tokens is not None:
            store.save(provider, tokens)
            # Auto-wire the engine for any provider that exposes both
            # `models_base_url` and `default_model` on its flow. GitHub
            # and Google both do; OpenAI/Anthropic don't (and their
            # subscriptions don't grant API access anyway).
            if hasattr(flow, "models_base_url") and hasattr(flow, "default_model"):
                _wire_oauth_engine(provider, flow, tokens, typer.echo)
            typer.echo(f"phantom auth login: {provider!r} OK ✓")
            raise typer.Exit(0)
        time.sleep(interval)

    typer.echo(
        f"phantom auth login: gave up after {max_minutes} minutes "
        "without authorisation.",
        err=True,
    )
    raise typer.Exit(1)


def logout(
    provider: str = typer.Option(
        ..., "--provider", "-p",
        help="OAuth provider: openai | anthropic | google",
    ),
) -> None:
    """Forget the local tokens for *provider*."""
    if provider not in _FLOWS:
        typer.echo(
            f"phantom auth logout: unknown provider {provider!r}",
            err=True,
        )
        raise typer.Exit(2)
    TokenStore.default().delete(provider)
    typer.echo(f"phantom auth logout: {provider!r} forgotten")


def status(
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON.",
    ),
) -> None:
    """Show which providers have local tokens."""
    store = TokenStore.default()
    rows: list[dict[str, Any]] = []
    for name in sorted(_FLOWS):
        try:
            tokens = store.load(name)
        except LicenseError as exc:
            rows.append({
                "provider": name, "present": True,
                "valid": False, "error": exc.detail or str(exc),
            })
            continue
        if tokens is None:
            rows.append({
                "provider": name, "present": False,
                "valid": False, "error": "",
            })
            continue
        rows.append({
            "provider": name,
            "present": True,
            "valid": not tokens.expired(),
            "expires_at": tokens.expires_at,
            "has_refresh": bool(tokens.refresh_token),
            "error": "",
        })

    if json_output:
        typer.echo(json.dumps(rows, separators=(",", ":")))
        return
    typer.echo(f"{'PROVIDER':<10}{'PRESENT':<9}{'VALID':<7}NOTES")
    for row in rows:
        notes = row.get("error") or (
            "(refresh-token available)"
            if row.get("has_refresh") else ""
        )
        typer.echo(
            f"{row['provider']:<10}"
            f"{'yes' if row['present'] else 'no':<9}"
            f"{'yes' if row['valid'] else 'no':<7}"
            f"{notes}"
        )


def whoami(
    provider: str = typer.Option(
        "github", "--provider", "-p",
        help="Which provider to identify against. Supported: github, google.",
    ),
) -> None:
    """Probe the provider's identity endpoint with the saved token.

    * ``github`` → ``https://api.github.com/user``: prints login,
      display name, public email, plan, public-repo count.
    * ``google`` → ``https://www.googleapis.com/oauth2/v2/userinfo``:
      prints email, name, locale, verified-email status.
    """
    if provider not in ("github", "google"):
        typer.echo(
            f"phantom auth whoami: {provider!r} doesn't expose an identity "
            "endpoint compatible with this command yet.",
            err=True,
        )
        raise typer.Exit(2)
    store = TokenStore.default()
    tokens = store.load(provider)
    if tokens is None:
        typer.echo(
            f"phantom auth whoami: not logged in. Run "
            f"`phantom auth login --provider {provider}` first.",
            err=True,
        )
        raise typer.Exit(1)
    if tokens.expired():
        typer.echo(
            "phantom auth whoami: token has expired. Re-run login.",
            err=True,
        )
        raise typer.Exit(1)

    flow = build_flow(provider)
    try:
        info = flow.whoami(tokens.access_token)  # type: ignore[attr-defined]
    except LicenseError as exc:
        typer.echo(f"phantom auth whoami: {exc.detail or exc}", err=True)
        raise typer.Exit(1)
    except AttributeError:
        typer.echo(
            f"phantom auth whoami: {provider!r} flow does not implement whoami.",
            err=True,
        )
        raise typer.Exit(2)

    if provider == "github":
        login_name = info.get("login", "?")
        name = info.get("name") or login_name
        email = info.get("email") or "(email not public)"
        plan = (info.get("plan") or {}).get("name", "free")
        public_repos = info.get("public_repos", 0)
        typer.echo(f"GitHub login   : {login_name}")
        typer.echo(f"Display name   : {name}")
        typer.echo(f"Public email   : {email}")
        typer.echo(f"GitHub plan    : {plan}")
        typer.echo(f"Public repos   : {public_repos}")
        typer.echo("")
        typer.echo("GitHub Models endpoint configured at "
                   "https://models.github.ai/inference (default model: gpt-4o)")
    else:  # google
        email = info.get("email", "(unknown)")
        name = info.get("name", "(unknown)")
        verified = info.get("verified_email", False)
        locale = info.get("locale", "?")
        typer.echo(f"Google email   : {email}")
        typer.echo(f"Display name   : {name}")
        typer.echo(f"Email verified : {'yes' if verified else 'no'}")
        typer.echo(f"Locale         : {locale}")
        typer.echo("")
        typer.echo("Gemini endpoint configured at "
                   "https://generativelanguage.googleapis.com/v1beta/openai "
                   "(default model: gemini-2.0-flash)")


def _wire_oauth_engine(
    provider: str, flow: Any, tokens: TokenSet, echo: Any,
) -> None:
    """After a successful OAuth login, configure the engine so the user
    can run ``phantom chat`` immediately without a separate setup step.

    Works for any provider whose flow exposes ``models_base_url`` and
    ``default_model``. Currently: GitHub (Models) and Google (Gemini
    via the OpenAI-compat endpoint).

    Writes three values into the legacy ``omnicli`` config (which is
    what both v3 ``python run.py chat`` and v4 ``phantom chat`` read):

    * ``main_api_key``  ← the OAuth access token
    * ``main_url``      ← provider's OpenAI-compat base URL
    * ``main_model``    ← provider's default model

    Best-effort: a missing or older omnicli is logged and swallowed —
    the OAuth token itself is already saved by :class:`TokenStore`, so
    a v4-only client can still read it from there.
    """
    try:
        from omnicli.auth import save_api_key
        from omnicli.memory import save_config
    except Exception as exc:  # pragma: no cover - older omnicli
        echo(f"  (note: could not auto-wire omnicli config: {exc})")
        return

    try:
        save_api_key(tokens.access_token)
        save_config("main_url", flow.models_base_url)
        save_config("main_model", flow.default_model)
    except Exception as exc:  # pragma: no cover - SQLite locked etc.
        echo(f"  (note: could not write engine config: {exc})")
        return

    # Identity probe — non-fatal if it fails. Gives the user instant
    # feedback that the token works against the provider's API.
    try:
        info = flow.whoami(tokens.access_token)
    except LicenseError as exc:
        echo(f"  (note: token saved but identity probe failed: "
             f"{exc.detail or exc})")
        return
    except Exception as exc:
        echo(f"  (note: token saved but identity probe failed: {exc})")
        return

    if provider == "github":
        who = "@" + str(info.get("login", "?"))
        product = "GitHub Models"
    elif provider == "google":
        who = str(info.get("email") or info.get("name") or "?")
        product = "Google Gemini (via Generative Language API)"
    else:
        who = "?"
        product = provider
    echo(
        f"  Logged in as {who} · {product} · "
        f"default model {flow.default_model} · base {flow.models_base_url}"
    )



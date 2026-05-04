"""Weather plugin — Open-Meteo lookup.

Free public API, no key required. Uses the loader-supplied HTTP client
when available (``ctx.extras['http']``), otherwise falls back to a
``urllib`` import (no network in the test environment by default).

Capability declared: ``network``. The loader's sandbox policy
will permit egress for this plugin only.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from phantom.errors import PluginError
from phantom.plugins.capability import Capability
from phantom.plugins.plugin import Plugin, PluginContext

__all__ = ["WeatherPlugin"]

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherPlugin(Plugin):
    """Look up current weather for a (lat, lon) pair.

    Payload schema::

        {"lat": <float>, "lon": <float>, "timezone": "auto" | "<tz>" }

    Result schema::

        {"temperature_c": <float>, "windspeed_kmh": <float>, "code": <int>}
    """

    def call(self, ctx: PluginContext, payload: dict[str, Any]) -> dict[str, Any]:
        if Capability.NETWORK not in ctx.capabilities:
            raise PluginError("weather plugin requires the 'network' capability")
        try:
            lat = float(payload["lat"])
            lon = float(payload["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginError("weather payload requires numeric 'lat' and 'lon'") from exc

        tz = payload.get("timezone", "auto")
        if not isinstance(tz, str):
            raise PluginError("weather payload 'timezone' must be a string")

        url = (
            _OPEN_METEO_URL
            + "?"
            + urlencode({
                "latitude": lat,
                "longitude": lon,
                "current_weather": "true",
                "timezone": tz,
            })
        )

        # If the loader supplied a richer HTTP client (e.g. httpx with a
        # mock), use it; otherwise hit the network directly.
        http = ctx.extras.get("http")
        if http is not None:
            response = http.get(url)
            data = response.json()
        else:  # pragma: no cover — exercised only with real network
            with urlopen(url, timeout=10) as resp:  # noqa: S310 — network call
                data = json.loads(resp.read().decode("utf-8"))

        cw = data.get("current_weather") or {}
        return {
            "temperature_c": cw.get("temperature"),
            "windspeed_kmh": cw.get("windspeed"),
            "code": cw.get("weathercode"),
        }

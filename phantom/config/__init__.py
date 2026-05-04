"""phantom.config — runtime configuration registries.

Re-exports the legacy v3-derived ``Config`` / ``SandboxConfig`` API from
``phantom.config.main`` *and* the v1.0 custom-provider registry from
``phantom.config.providers``. The two coexist so existing tests keep
passing while the new ``phantom config provider custom`` UX gets a
clean home.
"""

from __future__ import annotations

from phantom.config.main import (
    Config,
    SandboxConfig,
    default_config_path,
)
from phantom.config.providers import (
    CustomProvider,
    ProviderRegistry,
    providers_path,
)

__all__ = [
    "Config",
    "CustomProvider",
    "ProviderRegistry",
    "SandboxConfig",
    "default_config_path",
    "providers_path",
]

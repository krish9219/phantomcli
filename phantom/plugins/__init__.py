"""Phantom plugin SDK — third-party extension surface.

A Phantom plugin is a Python module that declares a :class:`PluginManifest`
and exports a :class:`Plugin` subclass. The :class:`PluginLoader`
discovers plugins by scanning a directory, validates each manifest
against a JSON schema, and exposes them through a typed registry.

Plugins run inside the Stage-1 sandbox: the loader builds a
:class:`phantom.sandbox.SandboxPolicy` from the plugin's declared
capabilities. A plugin that declares ``Capability.NETWORK`` gets
network-on; a plugin that declares ``Capability.EXECUTOR`` gets shell
access; everything else is read-only and offline.

ADR-0001 explains why the SDK is open-source MIT.
"""

from __future__ import annotations

from phantom.plugins.capability import Capability
from phantom.plugins.loader import PluginLoader, load_plugin
from phantom.plugins.manifest import PluginManifest
from phantom.plugins.plugin import Plugin, PluginContext
from phantom.plugins.registry import PluginRegistry
from phantom.plugins.signature import verify_signature

__all__ = [
    "Capability",
    "Plugin",
    "PluginContext",
    "PluginLoader",
    "PluginManifest",
    "PluginRegistry",
    "load_plugin",
    "verify_signature",
]

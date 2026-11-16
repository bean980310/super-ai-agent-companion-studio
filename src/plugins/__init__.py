# Plugin system
"""Discover and load optional features without touching the main app.

Plugins are folders containing a ``plugin.toml`` manifest and a Python module
that exposes ``register(api)``. They are discovered from::

    $AI_COMPANION_PLUGINS_DIRS
    ~/.ai-companion/plugins      # user plugins (git clone target)
    ./plugins                    # plugins shipped with the checkout

Only approved plugins are imported. Approval is stored in
``~/.ai-companion/plugins.json`` and bound to a fingerprint of the plugin
source, so a freshly cloned or updated plugin stays inert until the user
approves it in Settings > Plugins.
"""

from .api import PluginAPI, PluginContributions, PluginPage, PluginSettingsTab
from .models import (
    MANIFEST_FILE_NAME,
    SUPPORTED_API_VERSION,
    PluginError,
    PluginManifest,
    compute_fingerprint,
    is_valid_plugin_id,
    parse_manifest,
    slugify_plugin_id,
)
from .registry import (
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_REJECTED,
    Plugin,
    PluginRegistry,
    default_plugin_roots,
    get_plugin_registry,
)

__all__ = [
    "MANIFEST_FILE_NAME",
    "STATUS_APPROVED",
    "STATUS_DISABLED",
    "STATUS_ERROR",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "SUPPORTED_API_VERSION",
    "Plugin",
    "PluginAPI",
    "PluginContributions",
    "PluginError",
    "PluginManifest",
    "PluginPage",
    "PluginRegistry",
    "PluginSettingsTab",
    "compute_fingerprint",
    "default_plugin_roots",
    "get_plugin_registry",
    "is_valid_plugin_id",
    "parse_manifest",
    "slugify_plugin_id",
]

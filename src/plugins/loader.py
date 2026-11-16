# Importing a plugin's entrypoint module and running ``register(api)``.
"""Plugin code is imported under a private namespace.

Each plugin is imported as ``ai_companion_plugins.<id>`` with the plugin folder
as its package path, so relative imports (``from .helpers import x``) work and
two plugins can ship a module with the same name.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING

from ai_companion_core import logger

from .api import PluginAPI, PluginContributions
from .models import PluginError

if TYPE_CHECKING:  # pragma: no cover
    from .registry import Plugin

NAMESPACE = "ai_companion_plugins"


def _ensure_namespace() -> None:
    if NAMESPACE in sys.modules:
        return
    namespace = types.ModuleType(NAMESPACE)
    namespace.__path__ = []  # type: ignore[attr-defined]
    namespace.__package__ = NAMESPACE
    sys.modules[NAMESPACE] = namespace


def _entrypoint_path(plugin: Plugin) -> Path:
    """Resolve the entry module to a file, trying a few conventional layouts."""
    directory = plugin.directory
    module = plugin.manifest.module
    candidates = [
        directory / f"{module}.py",
        directory / module / "__init__.py",
        directory / "__init__.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise PluginError(
        f"Plugin '{plugin.id}' entrypoint module '{module}' not found in {directory}"
    )


def import_plugin_module(plugin: Plugin) -> types.ModuleType:
    """Import the plugin's entry module without running ``register``."""
    _ensure_namespace()
    target = _entrypoint_path(plugin)
    module_name = f"{NAMESPACE}.{plugin.id}"

    spec = importlib.util.spec_from_file_location(
        module_name,
        target,
        submodule_search_locations=[str(plugin.directory)],
    )
    if spec is None or spec.loader is None:
        raise PluginError(f"Could not create an import spec for {target}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    # Absolute imports inside the plugin (``import helpers``) resolve against
    # the plugin folder while the module body executes.
    added = str(plugin.directory) not in sys.path
    if added:
        sys.path.insert(0, str(plugin.directory))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise PluginError(f"Failed to import plugin '{plugin.id}': {exc}") from exc
    finally:
        if added:
            try:
                sys.path.remove(str(plugin.directory))
            except ValueError:
                pass
    return module


def load_plugin(plugin: Plugin, data_dir: Path | None = None) -> PluginContributions:
    """Import a plugin and run its ``register(api)`` entrypoint."""
    module = import_plugin_module(plugin)
    register = getattr(module, plugin.manifest.attribute, None)
    if not callable(register):
        raise PluginError(
            f"Plugin '{plugin.id}' has no callable '{plugin.manifest.attribute}' "
            f"in '{plugin.manifest.module}'"
        )

    contributions = PluginContributions()
    api = PluginAPI(
        plugin_id=plugin.id,
        data_dir=data_dir or (plugin.directory / ".data"),
        contributions=contributions,
    )
    try:
        register(api)
    except Exception as exc:
        raise PluginError(f"Plugin '{plugin.id}' failed during register(): {exc}") from exc

    logger.info(
        f"Loaded plugin '{plugin.id}' "
        f"(pages={len(contributions.pages)}, settings_tabs={len(contributions.settings_tabs)}, "
        f"routers={len(contributions.routers)})"
    )
    return contributions

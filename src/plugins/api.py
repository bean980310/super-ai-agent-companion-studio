# The object handed to a plugin's ``register(api)`` entrypoint.
"""Plugins extend the app through a small, explicit API surface.

A plugin module exposes ``register(api)`` and calls the ``register_*`` methods
below. Everything is collected into :class:`PluginContributions` so the host can
render pages, mount routes, and report what a plugin added.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_companion_core import logger as _logger

from .models import PluginError, is_valid_plugin_id


@dataclass(frozen=True)
class PluginPage:
    """A top-level page contributed by a plugin."""

    plugin_id: str
    id: str
    label: str
    builder: Callable[[], Any]
    order: int = 100


@dataclass(frozen=True)
class PluginSettingsTab:
    """A settings tab contributed by a plugin."""

    plugin_id: str
    label: str
    builder: Callable[[], Any]
    order: int = 100


@dataclass
class PluginContributions:
    """Everything a single plugin registered."""

    pages: list[PluginPage] = field(default_factory=list)
    settings_tabs: list[PluginSettingsTab] = field(default_factory=list)
    routers: list[Any] = field(default_factory=list)


class PluginAPI:
    """Registration API passed to ``register(api)``.

    Page builders are called inside the host's ``gr.TabItem`` context, so they
    should create Gradio components rather than their own ``gr.Blocks``.
    """

    def __init__(
        self,
        plugin_id: str,
        data_dir: Path,
        contributions: PluginContributions,
    ) -> None:
        self.plugin_id = plugin_id
        self._data_dir = Path(data_dir)
        self._contributions = contributions

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def logger(self):
        """Shared application logger."""
        return _logger

    @property
    def config_dir(self) -> Path:
        """Per-plugin directory for persistent data, created on first access."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_page(
        self,
        id: str,
        label: str,
        builder: Callable[[], Any],
        order: int = 100,
    ) -> PluginPage:
        """Add a top-level page (sidebar entry + tab).

        ``id`` must be unique across the app; ``builder`` is called inside the
        tab and should create Gradio components.
        """
        page_id = str(id or "").strip()
        if not is_valid_plugin_id(page_id):
            raise PluginError(f"Invalid page id '{page_id}' (use lowercase letters, digits, '-' or '_')")
        if not str(label or "").strip():
            raise PluginError(f"Page '{page_id}' needs a non-empty label")
        if not callable(builder):
            raise PluginError(f"Page '{page_id}' builder must be callable")
        if any(page.id == page_id for page in self._contributions.pages):
            raise PluginError(f"Page '{page_id}' is already registered by this plugin")

        page = PluginPage(
            plugin_id=self.plugin_id,
            id=page_id,
            label=str(label).strip(),
            builder=builder,
            order=int(order),
        )
        self._contributions.pages.append(page)
        return page

    def register_settings_tab(
        self,
        label: str,
        builder: Callable[[], Any],
        order: int = 100,
    ) -> PluginSettingsTab:
        """Add a tab to the settings popup."""
        if not str(label or "").strip():
            raise PluginError("A settings tab needs a non-empty label")
        if not callable(builder):
            raise PluginError("Settings tab builder must be callable")
        if any(tab.label == str(label).strip() for tab in self._contributions.settings_tabs):
            raise PluginError(f"Settings tab '{label}' is already registered by this plugin")

        tab = PluginSettingsTab(
            plugin_id=self.plugin_id,
            label=str(label).strip(),
            builder=builder,
            order=int(order),
        )
        self._contributions.settings_tabs.append(tab)
        return tab

    def register_api_route(self, router: Any) -> Any:
        """Mount a FastAPI ``APIRouter`` on the application."""
        if not hasattr(router, "routes"):
            raise PluginError("register_api_route expects a FastAPI APIRouter")
        self._contributions.routers.append(router)
        return router

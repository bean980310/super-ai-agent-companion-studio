# Filesystem-backed plugin registry with an approval gate.
"""Discover plugins, remember which ones the user approved, and load them.

Discovery only reads ``plugin.toml`` files. A plugin's Python code is imported
only when it is both approved and enabled, which is what makes the "ask on
first run" flow possible: a freshly cloned plugin shows up as *pending* and is
never executed until the user approves it in Settings > Plugins.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_companion_core import logger

from src.common.apppath import APPDATA_PATH

from .api import PluginContributions, PluginPage, PluginSettingsTab
from .loader import load_plugin
from .models import (
    MANIFEST_FILE_NAME,
    PluginError,
    PluginManifest,
    compute_fingerprint,
    parse_manifest,
    slugify_plugin_id,
)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_DISABLED = "disabled"
STATUS_ERROR = "error"

_SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}


@dataclass
class Plugin:
    """A discovered plugin folder."""

    manifest: PluginManifest
    directory: Path
    source: str
    fingerprint: str

    @property
    def id(self) -> str:
        return self.manifest.id

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.manifest.to_dict(),
            "directory": str(self.directory),
            "source": self.source,
            "fingerprint": self.fingerprint,
        }


def default_plugin_roots() -> list[Path]:
    """Return plugin search roots, highest priority first."""
    roots: list[Path] = []

    env_dirs = os.environ.get("AI_COMPANION_PLUGINS_DIRS", "")
    for part in env_dirs.split(os.pathsep):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser())

    roots.append(APPDATA_PATH / "plugins")
    roots.append(Path.cwd() / "plugins")

    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


class PluginRegistry:
    """Discovers plugin folders and tracks approval/enabled state.

    State is persisted to ``plugins.json`` under the application data
    directory. Approvals are keyed by plugin id and bound to a fingerprint of
    the plugin's source, so updating a plugin requires approving it again.
    """

    def __init__(
        self,
        roots: Sequence[Path | str] | None = None,
        config_path: Path | str | None = None,
        user_root: Path | str | None = None,
        data_root: Path | str | None = None,
    ) -> None:
        self.user_root = Path(user_root).expanduser() if user_root else APPDATA_PATH / "plugins"
        configured = [Path(r).expanduser() for r in roots] if roots is not None else default_plugin_roots()
        configured = [r for r in configured if not self._same_path(r, self.user_root)]
        configured.insert(0, self.user_root)
        self.roots: list[Path] = configured
        self.config_path = Path(config_path).expanduser() if config_path else APPDATA_PATH / "plugins.json"
        self.data_root = Path(data_root).expanduser() if data_root else APPDATA_PATH / "plugin-data"

        self._plugins: dict[str, Plugin] = {}
        self._approved: dict[str, dict[str, str]] = {}
        self._rejected: set[str] = set()
        self._disabled: set[str] = set()
        self._errors: dict[str, str] = {}
        self._contributions: dict[str, PluginContributions] = {}
        self._loaded: set[str] = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @staticmethod
    def _same_path(a: Path, b: Path) -> bool:
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return a == b

    def _source_label(self, root: Path) -> str:
        if self._same_path(root, self.user_root):
            return "user"
        try:
            return root.resolve().relative_to(Path.cwd().resolve()).as_posix() or "."
        except (OSError, ValueError):
            pass
        try:
            return f"~/{root.resolve().relative_to(Path.home().resolve()).as_posix()}"
        except (OSError, ValueError):
            return str(root)

    def refresh(self) -> list[Plugin]:
        """Rescan all roots and return the discovered plugins."""
        with self._lock:
            self._load_config()
            plugins: dict[str, Plugin] = {}
            for root in self.roots:
                root = Path(root).expanduser()
                if not root.is_dir():
                    continue
                label = self._source_label(root)
                try:
                    children = sorted(root.iterdir())
                except OSError as exc:
                    logger.warning(f"Failed to scan plugin root {root}: {exc}")
                    continue
                for child in children:
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                    if child.name in _SKIP_DIR_NAMES:
                        continue
                    manifest_path = child / MANIFEST_FILE_NAME
                    if not manifest_path.is_file():
                        continue
                    try:
                        manifest = parse_manifest(manifest_path, fallback_id=child.name)
                    except PluginError as exc:
                        logger.warning(f"Skipping invalid plugin at {child}: {exc}")
                        continue
                    if manifest.id in plugins:
                        logger.warning(
                            f"Duplicate plugin id '{manifest.id}' at {child}; "
                            f"keeping {plugins[manifest.id].directory}"
                        )
                        continue
                    plugins[manifest.id] = Plugin(
                        manifest=manifest,
                        directory=child,
                        source=label,
                        fingerprint=compute_fingerprint(child),
                    )

            self._plugins = plugins
            # Drop stale error/contribution state for plugins that disappeared.
            self._errors = {k: v for k, v in self._errors.items() if k in plugins}
            self._contributions = {k: v for k, v in self._contributions.items() if k in plugins}
            self._loaded &= set(plugins)
            return self._sorted_plugins()

    def _ensure_discovered(self) -> None:
        if not self._plugins:
            self.refresh()

    def _sorted_plugins(self) -> list[Plugin]:
        return [self._plugins[key] for key in sorted(self._plugins)]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        approved: dict[str, dict[str, str]] = {}
        rejected: set[str] = set()
        disabled: set[str] = set()
        try:
            if self.config_path.is_file():
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    raw_approved = data.get("approved", {})
                    if isinstance(raw_approved, dict):
                        for key, value in raw_approved.items():
                            if isinstance(value, dict):
                                approved[str(key)] = {
                                    "fingerprint": str(value.get("fingerprint", "")),
                                    "approved_at": str(value.get("approved_at", "")),
                                }
                            elif isinstance(value, str):
                                approved[str(key)] = {"fingerprint": value, "approved_at": ""}
                    for key, target in (("rejected", rejected), ("disabled", disabled)):
                        raw = data.get(key, [])
                        if isinstance(raw, list):
                            target.update(str(item) for item in raw)
        except Exception as exc:
            logger.warning(f"Failed to read plugin config {self.config_path}: {exc}")
        self._approved = approved
        self._rejected = rejected
        self._disabled = disabled

    def _save_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "approved": self._approved,
                "rejected": sorted(self._rejected),
                "disabled": sorted(self._disabled),
            }
            self.config_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Failed to write plugin config {self.config_path}: {exc}")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def list_plugins(self) -> list[Plugin]:
        with self._lock:
            self._ensure_discovered()
            return self._sorted_plugins()

    def get(self, plugin_id: str) -> Plugin | None:
        with self._lock:
            self._ensure_discovered()
            return self._plugins.get(str(plugin_id))

    def status(self, plugin_id: str) -> str:
        with self._lock:
            self._ensure_discovered()
            plugin = self._plugins.get(str(plugin_id))
            if plugin is None:
                return STATUS_ERROR
            if plugin_id in self._rejected:
                return STATUS_REJECTED
            if plugin_id in self._disabled:
                return STATUS_DISABLED
            approval = self._approved.get(plugin_id)
            if approval and approval.get("fingerprint") == plugin.fingerprint:
                return STATUS_ERROR if plugin_id in self._errors else STATUS_APPROVED
            return STATUS_PENDING

    def error(self, plugin_id: str) -> str:
        with self._lock:
            return self._errors.get(str(plugin_id), "")

    def is_approved(self, plugin_id: str) -> bool:
        return self.status(plugin_id) == STATUS_APPROVED

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def _require(self, plugin_id: str) -> Plugin:
        self._ensure_discovered()
        plugin = self._plugins.get(str(plugin_id))
        if plugin is None:
            raise PluginError(f"Unknown plugin: {plugin_id}")
        return plugin

    def approve(self, plugin_id: str) -> Plugin:
        """Approve the plugin's current source and clear any previous error."""
        with self._lock:
            plugin = self._require(plugin_id)
            self._approved[plugin.id] = {
                "fingerprint": plugin.fingerprint,
                "approved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            self._rejected.discard(plugin.id)
            self._disabled.discard(plugin.id)
            self._errors.pop(plugin.id, None)
            self._save_config()
            return plugin

    def reject(self, plugin_id: str) -> Plugin:
        with self._lock:
            plugin = self._require(plugin_id)
            self._rejected.add(plugin.id)
            self._approved.pop(plugin.id, None)
            self._errors.pop(plugin.id, None)
            self._save_config()
            return plugin

    def set_enabled(self, plugin_id: str, enabled: bool) -> Plugin:
        with self._lock:
            plugin = self._require(plugin_id)
            if enabled:
                self._disabled.discard(plugin.id)
            else:
                self._disabled.add(plugin.id)
            self._save_config()
            return plugin

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load_approved(self) -> list[str]:
        """Import and register every approved, enabled plugin.

        Failures are recorded per plugin and never abort the host app.
        """
        with self._lock:
            self._ensure_discovered()
            loaded: list[str] = []
            for plugin in self._sorted_plugins():
                if plugin.id in self._loaded:
                    continue
                if self.status(plugin.id) != STATUS_APPROVED:
                    continue
                try:
                    contributions = load_plugin(plugin, data_dir=self.data_root / plugin.id)
                except Exception as exc:
                    self._errors[plugin.id] = str(exc)
                    logger.warning(f"Plugin '{plugin.id}' failed to load: {exc}")
                    continue
                self._contributions[plugin.id] = contributions
                self._loaded.add(plugin.id)
                loaded.append(plugin.id)
            return loaded

    def is_loaded(self, plugin_id: str) -> bool:
        with self._lock:
            return str(plugin_id) in self._loaded

    def contributions(self, plugin_id: str) -> PluginContributions | None:
        with self._lock:
            return self._contributions.get(str(plugin_id))

    def pages(self) -> list[PluginPage]:
        with self._lock:
            pages = [page for item in self._contributions.values() for page in item.pages]
            return sorted(pages, key=lambda page: (page.order, page.label.lower()))

    def settings_tabs(self) -> list[PluginSettingsTab]:
        with self._lock:
            tabs = [tab for item in self._contributions.values() for tab in item.settings_tabs]
            return sorted(tabs, key=lambda tab: (tab.order, tab.label.lower()))

    def api_routers(self) -> list[Any]:
        with self._lock:
            return [router for item in self._contributions.values() for router in item.routers]

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------
    def clone(self, url: str, name: str | None = None) -> Plugin:
        """Clone a plugin repository into the user plugin folder."""
        url = str(url or "").strip()
        if not url:
            raise PluginError("A repository URL is required")

        parsed = urlparse(url)
        if not (parsed.scheme in {"https", "http", "ssh", "git"} or url.startswith("git@")):
            raise PluginError("Only git repository URLs are supported")

        folder = slugify_plugin_id(name or Path(parsed.path).stem or "plugin")
        destination = self.user_root / folder
        if destination.exists():
            raise PluginError(f"Plugin folder already exists: {destination}")

        self.user_root.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", url, str(destination)],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except FileNotFoundError as exc:
            raise PluginError("git is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise PluginError("git clone timed out") from exc

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            raise PluginError(f"git clone failed: {message or 'unknown error'}")

        self.refresh()
        for plugin in self._plugins.values():
            if self._same_path(plugin.directory, destination):
                return plugin
        raise PluginError(
            f"Cloned into {destination}, but no {MANIFEST_FILE_NAME} was found at its root"
        )


_registry: PluginRegistry | None = None
_registry_lock = threading.Lock()


def get_plugin_registry() -> PluginRegistry:
    """Return the process-wide plugin registry."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = PluginRegistry()
        return _registry

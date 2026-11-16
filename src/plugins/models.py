# Plugin manifest parsing, validation, and fingerprinting.
"""Plugins are folders that contain a ``plugin.toml`` manifest.

A plugin folder looks like::

    plugins/my-feature/
        plugin.toml
        plugin.py        # exposes ``register(api)``

The manifest declares identity and the entrypoint::

    [plugin]
    id = "my-feature"
    name = "My Feature"
    version = "0.1.0"
    entrypoint = "plugin:register"
    api_version = 1
    description = "What this plugin adds."

Only the manifest is read during discovery. The entrypoint module is imported
later, and only after the user has approved the plugin.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILE_NAME = "plugin.toml"
SUPPORTED_API_VERSION = 1
DEFAULT_ENTRYPOINT = "plugin:register"

_ID_RE = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
_ENTRYPOINT_RE = re.compile(r"\A([A-Za-z_][A-Za-z0-9_]*):([A-Za-z_][A-Za-z0-9_]*)\Z")
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")

# Files that make up a plugin's executable surface. Used for fingerprinting so
# that an updated plugin has to be approved again.
_FINGERPRINT_SUFFIXES = {".py", ".toml", ".json", ".yaml", ".yml"}
_SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}


class PluginError(Exception):
    """Raised when a plugin cannot be discovered, parsed, or loaded."""


def slugify_plugin_id(value: str) -> str:
    """Turn an arbitrary folder name into a valid plugin id."""
    slug = _SLUG_RE.sub("-", str(value or "").strip().lower()).strip("-_")
    return slug or "plugin"


def is_valid_plugin_id(value: str) -> bool:
    return bool(_ID_RE.match(str(value or "")))


@dataclass
class PluginManifest:
    """Declarative description of a plugin."""

    id: str
    name: str
    version: str = "0.0.0"
    entrypoint: str = DEFAULT_ENTRYPOINT
    description: str = ""
    author: str = ""
    homepage: str = ""
    api_version: int = SUPPORTED_API_VERSION
    order: int = 100
    requires: list[str] = field(default_factory=list)

    @property
    def module(self) -> str:
        return self.entrypoint.split(":", 1)[0]

    @property
    def attribute(self) -> str:
        return self.entrypoint.split(":", 1)[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "entrypoint": self.entrypoint,
            "description": self.description,
            "author": self.author,
            "homepage": self.homepage,
            "api_version": self.api_version,
            "order": self.order,
            "requires": list(self.requires),
        }


def _as_str(section: dict[str, Any], key: str, default: str = "") -> str:
    value = section.get(key, default)
    if value is None:
        return default
    if not isinstance(value, (str, int, float)):
        raise PluginError(f"Manifest field '{key}' must be a string")
    return str(value).strip()


def _as_int(section: dict[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PluginError(f"Manifest field '{key}' must be an integer")
    return value


def parse_manifest(path: Path | str, fallback_id: str | None = None) -> PluginManifest:
    """Read and validate a ``plugin.toml`` file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PluginError(f"Manifest not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise PluginError(f"Failed to read manifest {path}: {exc}") from exc

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PluginError(f"Invalid TOML in {path}: {exc}") from exc

    section = raw.get("plugin")
    if not isinstance(section, dict):
        # Allow flat manifests that put the fields at the top level.
        section = raw

    plugin_id = _as_str(section, "id") or (fallback_id or "")
    plugin_id = slugify_plugin_id(plugin_id)
    if not is_valid_plugin_id(plugin_id):
        raise PluginError(f"Invalid plugin id '{plugin_id}' (use lowercase letters, digits, '-' or '_')")

    entrypoint = _as_str(section, "entrypoint", DEFAULT_ENTRYPOINT) or DEFAULT_ENTRYPOINT
    if not _ENTRYPOINT_RE.match(entrypoint):
        raise PluginError(f"Invalid entrypoint '{entrypoint}' (expected 'module:function')")

    api_version = _as_int(section, "api_version", SUPPORTED_API_VERSION)
    if api_version < 1:
        raise PluginError(f"Invalid api_version {api_version}")
    if api_version > SUPPORTED_API_VERSION:
        raise PluginError(
            f"Plugin '{plugin_id}' requires plugin API v{api_version}, "
            f"but this build supports up to v{SUPPORTED_API_VERSION}"
        )

    requires = section.get("requires", [])
    if isinstance(requires, str):
        requires = [requires]
    if not isinstance(requires, list) or any(not isinstance(item, str) for item in requires):
        raise PluginError("Manifest field 'requires' must be a list of strings")

    return PluginManifest(
        id=plugin_id,
        name=_as_str(section, "name") or plugin_id,
        version=_as_str(section, "version") or "0.0.0",
        entrypoint=entrypoint,
        description=_as_str(section, "description"),
        author=_as_str(section, "author"),
        homepage=_as_str(section, "homepage"),
        api_version=api_version,
        order=_as_int(section, "order", 100),
        requires=list(requires),
    )


def compute_fingerprint(directory: Path | str) -> str:
    """Hash the plugin's manifest and source files.

    The fingerprint is stored alongside an approval. If the plugin's code
    changes (for example after ``git pull``), the fingerprint no longer
    matches and the plugin has to be approved again.
    """
    directory = Path(directory)
    digest = hashlib.sha256()
    entries: list[tuple[str, Path]] = []
    try:
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(directory)
            if any(part.startswith(".") or part in _SKIP_DIR_NAMES for part in relative.parts):
                continue
            if path.suffix.lower() not in _FINGERPRINT_SUFFIXES:
                continue
            entries.append((relative.as_posix(), path))
    except OSError:
        return "sha256:unreadable"

    for relative, path in sorted(entries):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"
